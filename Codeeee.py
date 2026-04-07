#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time
import subprocess
import base64
import os
import speech_recognition as sr
from openai import OpenAI
from gtts import gTTS

# -----------------------------
# Pin definitions (BOARD mode)
# -----------------------------
LeftVibrator = 13
RightVibrator = 12
PushButton = 15
LeftUltrasonicTrig = 31
LeftUltrasonicEcho = 29
RightUltrasonicTrig = 38
RightUltrasonicEcho = 40

# -----------------------------
# Settings
# -----------------------------
MIC_DEADZONE = 2
DISTANCE_THRESHOLD = 18  # inches

MODEL = "gpt-5-nano-2025-08-07"
IMAGE_PATH = "captured_image.jpg"
RESOLUTION = "640x480"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
r = sr.Recognizer()

# Start immediately instead of waiting on first mic cycle
last_mic_time = time.time() - MIC_DEADZONE

button_pressed = False


def capture_image(path: str):
    # Keep -S 2 if your camera needs warmup frames for a cleaner image
    subprocess.run(
        ["fswebcam", "-r", RESOLUTION, "-S", "2", "--no-banner", path],
        check=True
    )


def to_data_url(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def extract_text(resp):
    text = getattr(resp, "output_text", None)
    if text:
        return text.strip()

    try:
        for item in getattr(resp, "output", []):
            for part in getattr(item, "content", []):
                if getattr(part, "type", None) in ("output_text", "text") and getattr(part, "text", None):
                    return part.text.strip()
    except Exception:
        pass

    return str(resp)


def speak(text):
    try:
        tts = gTTS(text, lang="en")
        tts.save("response.mp3")
        subprocess.run(["mpg123", "response.mp3"], check=True)
    except Exception as e:
        print("Speak error:", e)


def listen():
    try:
        with sr.Microphone() as source:
            # Small calibration for ambient noise
            r.adjust_for_ambient_noise(source, duration=0.1)
            audio = r.listen(source, timeout=3, phrase_time_limit=4)
            return r.recognize_google(audio).strip()
    except Exception as e:
        print("Listen error:", e)
        return ""


def analyze_scene():
    capture_image(IMAGE_PATH)
    data_url = to_data_url(IMAGE_PATH)

    prompt = (
        "Reply with EXACTLY ONE short sentence (<= 15 words) "
        "describing the main visible object. Do not read text. "
        "Do not describe people. "
        "Format: 'there is a (color) (object) in front of you'. "
        "If none, say 'no object detected'."
    )

    resp = client.responses.create(
        model=MODEL,
        reasoning={"effort": "low"},
        max_output_tokens=100,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url}
            ]
        }],
    )

    return extract_text(resp)


def detect(channel):
    global button_pressed
    button_pressed = True


def handle_button():
    print("Button pressed - analyzing scene...")
    try:
        result = analyze_scene()
        print("AI:", result)
        speak(result)
    except Exception as e:
        print("Button/scene error:", e)


def clean_phrase(text: str) -> str:
    text = text.lower().strip()

    filler_words = {
        "is", "there", "a", "an", "the", "any", "do", "you", "see",
        "can", "find", "for", "me", "in", "front", "of"
    }

    words = [w for w in text.split() if w not in filler_words]
    return " ".join(words).strip()


def speechSearch(userInp):
    try:
        wanted = clean_phrase(userInp)

        if not wanted:
            speak("Please say the object name more clearly.")
            return

        result = analyze_scene()
        print("AI:", result)

        result_l = result.lower()
        wanted_words = wanted.split()

        # Require at least one meaningful word match, not filler words
        found = any(word in result_l for word in wanted_words)

        if found:
            speak(f"Yes, {wanted} is in front of you.")
        else:
            speak(f"No, {wanted} is not in front of you.")

    except Exception as e:
        print("Speech search error:", e)


def getDistance(trig_pin, echo_pin):
    GPIO.output(trig_pin, 0)
    time.sleep(0.000002)

    GPIO.output(trig_pin, 1)
    time.sleep(0.00001)
    GPIO.output(trig_pin, 0)

    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 0:
        if time.time() > timeout:
            return 999
    t1 = time.time()

    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 1:
        if time.time() > timeout:
            return 999
    t2 = time.time()

    return (t2 - t1) * 340 / 2 * 39.37


def setup():
    GPIO.setmode(GPIO.BOARD)

    GPIO.setup(LeftUltrasonicTrig, GPIO.OUT)
    GPIO.setup(LeftUltrasonicEcho, GPIO.IN)
    GPIO.setup(RightUltrasonicTrig, GPIO.OUT)
    GPIO.setup(RightUltrasonicEcho, GPIO.IN)

    GPIO.setup(PushButton, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LeftVibrator, GPIO.OUT)
    GPIO.setup(RightVibrator, GPIO.OUT)

    GPIO.add_event_detect(PushButton, GPIO.FALLING, callback=detect, bouncetime=200)

    print("System started. Distance sensing is active immediately.")


def loop():
    global last_mic_time, button_pressed

    while True:
        # -----------------------------
        # Continuous distance sensing
        # -----------------------------
        lDis = getDistance(LeftUltrasonicTrig, LeftUltrasonicEcho)
        rDis = getDistance(RightUltrasonicTrig, RightUltrasonicEcho)

        print(f"Left: {lDis:.1f} in | Right: {rDis:.1f} in")

        GPIO.output(LeftVibrator, lDis < DISTANCE_THRESHOLD)
        GPIO.output(RightVibrator, rDis < DISTANCE_THRESHOLD)

        # -----------------------------
        # Explicit button trigger
        # -----------------------------
        if button_pressed:
            button_pressed = False
            handle_button()

        # -----------------------------
        # Voice trigger path
        # Mic is checked regularly, starting immediately
        # -----------------------------
        if time.time() - last_mic_time > MIC_DEADZONE:
            last_mic_time = time.time()
            print("Listening...")
            speech = listen()

            if speech:
                print("You said:", speech)
                speechSearch(speech)

        time.sleep(0.1)


def destroy():
    try:
        GPIO.output(LeftVibrator, GPIO.LOW)
        GPIO.output(RightVibrator, GPIO.LOW)
    except Exception:
        pass
    GPIO.cleanup()


if __name__ == "__main__":
    setup()
    try:
        loop()
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        destroy()