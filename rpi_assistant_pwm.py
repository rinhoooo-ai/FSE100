#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time
import subprocess
import base64
import sys
import os
from openai import OpenAI
from gtts import gTTS

LeftVibrator = 35       # gpio19 (hardware PWM)
RightVibrator = 12      # gpio18 (hardware PWM)
PushButton = 15         # gpio22
LeftUltrasonicTrig = 31 # gpio6
LeftUltrasonicEcho = 29 # gpio5
RightUltrasonicTrig = 38 # gpio20
RightUltrasonicEcho = 40 # gpio21

client = OpenAI(api_key="YOUR_API_KEY_HERE")

MODEL = "gpt-5-nano-2025-08-07"
IMAGE_PATH = "captured_image.jpg"
RESOLUTION = "640x480"
DISTANCE_THRESHOLD = 18  # inches

TTS_OUTPUT = "/tmp/tts_output.mp3"

# state diagram variables
state = 0
# state = 0 -----> Not in scanning state
# state = 1 -----> Enter camera scan state
# state = 2 -----> Waiting for second input after first scan
# state = 3 -----> AI object-location

state2StartTime = 0
lastObjectDescription = ""

LEFT_PWM = None
RIGHT_PWM = None


def speak(text: str):
    try:
        tts = gTTS(text=text, lang="en")
        tts.save(TTS_OUTPUT)
        subprocess.run(["mpg123", "-q", TTS_OUTPUT])
        os.remove(TTS_OUTPUT)
    except Exception as e:
        print(f"[TTS ERROR] {e}")


def capture_image(path: str):
    subprocess.run(["pkill", "-f", "fswebcam"], stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    subprocess.run(["fswebcam", "-r", RESOLUTION, "-S", "2", "--no-banner", path], check=True)

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
    try:
        return resp.model_dump_json(indent=2)
    except Exception:
        return str(resp)

def setup():
    global LEFT_PWM, RIGHT_PWM

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(LeftUltrasonicTrig, GPIO.OUT)
    GPIO.setup(LeftUltrasonicEcho, GPIO.IN)
    GPIO.setup(RightUltrasonicTrig, GPIO.OUT)
    GPIO.setup(RightUltrasonicEcho, GPIO.IN)
    GPIO.setup(PushButton, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LeftVibrator, GPIO.OUT)
    GPIO.setup(RightVibrator, GPIO.OUT)

    LEFT_PWM = GPIO.PWM(LeftVibrator, 100)   # 100Hz
    RIGHT_PWM = GPIO.PWM(RightVibrator, 100)
    LEFT_PWM.start(0)
    RIGHT_PWM.start(0)

    GPIO.add_event_detect(PushButton, GPIO.FALLING, callback=detect, bouncetime=200)


def distance_to_duty(distance):
    if distance >= DISTANCE_THRESHOLD:
        return 0
    duty = 100 - (distance / DISTANCE_THRESHOLD) * 90
    return max(10, min(100, duty))


def firstScan():
    global lastObjectDescription

    capture_image(IMAGE_PATH)
    data_url = to_data_url(IMAGE_PATH)

    prompt = (
        "Describe what you see in the image in front of you in 1-2 short sentences. "
        "Focus on the main subject, its color, shape, and surroundings if relevant. "
        "Keep it natural and concise. "
        "If the image is unclear or nothing is visible, reply with 'nothing visible'."
    )

    resp = client.responses.create(
        model=MODEL,
        reasoning={"effort": "low"},
        max_output_tokens=1024,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url}
            ]
        }],
    )

    ai_response = extract_text(resp)
    lastObjectDescription = ai_response
    print(f"AI Response (first scan): {ai_response}")
    speak(ai_response)


def secondScan():
    global lastObjectDescription

    capture_image(IMAGE_PATH)
    data_url = to_data_url(IMAGE_PATH)

    prompt = (
        f"The previous object description was: '{lastObjectDescription}'. "
        "Look at this new image and answer where that object is now. "
        "Reply with EXACTLY ONE short sentence (<= 15 words). "
        "Do not use a list. Do not use bullet points. "
        "Examples of acceptable style: "
        "'the bottle is near the chair', "
        "'the red bag is on the table', "
        "'the object is farther ahead', "
        "'object not found'. "
        "If the object cannot be found, reply exactly with 'object not found'."
    )

    resp = client.responses.create(
        model=MODEL,
        reasoning={"effort": "low"},
        max_output_tokens=1024,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url}
            ]
        }],
    )

    ai_response = extract_text(resp)
    print(f"AI Response (second scan): {ai_response}")
    speak(ai_response)


def detect(chn):
    global state, state2StartTime

    try:
        if state == 0:
            print("Button pressed: entering first scan state")
            speak("Button pressed. Scanning for objects now.")
            state = 1

            firstScan()

            state = 2
            state2StartTime = time.time()
            print("Now waiting up to 10 seconds for second button press")

        elif state == 2:
            print("Button pressed during 10-second window: entering second scan state")
            speak("Button pressed. Locating the object now.")
            state = 3

            secondScan()

            state = 0
            print("Returning to not scanning state")

        else:
            print("Button press ignored in current state")

    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        state = 0
        raise


def getDistance(trig_pin, echo_pin):
    GPIO.output(trig_pin, 0)
    time.sleep(0.000002)

    GPIO.output(trig_pin, 1)
    time.sleep(0.00001)
    GPIO.output(trig_pin, 0)

    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 0:
        if time.time() > timeout:
            print(f"[WARN] Echo timeout (waiting HIGH) on pin {echo_pin}")
            return 999
    time1 = time.time()

    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 1:
        if time.time() > timeout:
            print(f"[WARN] Echo timeout (waiting LOW) on pin {echo_pin}")
            return 999
    time2 = time.time()

    during = time2 - time1
    distance_inches = during * 340 / 2 * 39.37
    return distance_inches


def loop():
    global state, state2StartTime

    while True:
        lDis = getDistance(LeftUltrasonicTrig, LeftUltrasonicEcho)
        rDis = getDistance(RightUltrasonicTrig, RightUltrasonicEcho)

        print(f"Left: {lDis:.1f} in | Right: {rDis:.1f} in | State: {state}")

        LEFT_PWM.ChangeDutyCycle(distance_to_duty(lDis))
        RIGHT_PWM.ChangeDutyCycle(distance_to_duty(rDis))

        if state == 2:
            if time.time() - state2StartTime >= 10:
                print("10 seconds passed with no second input. Returning to state 0.")
                state = 0

        time.sleep(0.1)


def destroy():
    LEFT_PWM.stop()
    RIGHT_PWM.stop()
    GPIO.output(LeftVibrator, 0)
    GPIO.output(RightVibrator, 0)
    GPIO.cleanup()


if __name__ == "__main__":
    setup()
    try:
        loop()
    except KeyboardInterrupt:
        destroy()
