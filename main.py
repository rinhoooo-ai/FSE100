#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time
import subprocess
import base64
import sys
from openai import OpenAI

LeftVibrator = 13       # gpio27
RightVibrator = 12      # gpio18
PushButton = 15         # gpio22
LeftUltrasonicTrig = 31 # gpio6
LeftUltrasonicEcho = 29 # gpio5
RightUltrasonicTrig = 38 # gpio20
RightUltrasonicEcho = 40 # gpio21

client = OpenAI(api_key="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")

MODEL = "gpt-5-nano-2025-08-07"
IMAGE_PATH = "captured_image.jpg"
RESOLUTION = "640x480"
DISTANCE_THRESHOLD = 18  # inches

def capture_image(path: str):
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
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(LeftUltrasonicTrig, GPIO.OUT)
    GPIO.setup(LeftUltrasonicEcho, GPIO.IN)
    GPIO.setup(RightUltrasonicTrig, GPIO.OUT)
    GPIO.setup(RightUltrasonicEcho, GPIO.IN)
    GPIO.setup(PushButton, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LeftVibrator, GPIO.OUT)
    GPIO.setup(RightVibrator, GPIO.OUT)

    # Ensure vibrators are off on startup
    GPIO.output(LeftVibrator, 0)
    GPIO.output(RightVibrator, 0)

    GPIO.add_event_detect(PushButton, GPIO.BOTH, callback=detect, bouncetime=200)

def detect(chn):
    try:
        capture_image(IMAGE_PATH)
        data_url = to_data_url(IMAGE_PATH)

        prompt = (
            "Reply with EXACTLY ONE short sentence (<= 15 words) "
            "describing the main visible objects. Do not read text."
            "The format should be 'there is a (insert color of object) (insert object name) in front of you'."
            "If no object found, reply with 'no object detected'."
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
        print(f"AI Response: {ai_response}")

        from gtts import gTTS
        tts = gTTS(ai_response, lang="en")
        tts.save("response.mp3")
        subprocess.run(["mpg123", "response.mp3"], check=True)

    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        raise

def getDistance(trig_pin, echo_pin):
    """
    Measure distance using an ultrasonic sensor.
    Returns distance in inches.
    If timeout occurs (no echo received), returns 999 so the vibrator stays off.
    """
    # Ensure trig is LOW before starting
    GPIO.output(trig_pin, 0)
    time.sleep(0.000002)

    # Send 10 microsecond trigger pulse
    GPIO.output(trig_pin, 1)
    time.sleep(0.00001)
    GPIO.output(trig_pin, 0)

    # Wait for echo to go HIGH (50ms timeout)
    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 0:
        if time.time() > timeout:
            print(f"[WARN] Echo timeout (waiting HIGH) on pin {echo_pin}")
            return 999  # No object detected -> do not vibrate
    time1 = time.time()

    # Wait for echo to go LOW (50ms timeout)
    timeout = time.time() + 0.05
    while GPIO.input(echo_pin) == 1:
        if time.time() > timeout:
            print(f"[WARN] Echo timeout (waiting LOW) on pin {echo_pin}")
            return 999  # No object detected -> do not vibrate
    time2 = time.time()

    during = time2 - time1
    distance_inches = during * 340 / 2 * 39.37
    return distance_inches

def loop():
    while True:
        lDis = getDistance(LeftUltrasonicTrig, LeftUltrasonicEcho)
        rDis = getDistance(RightUltrasonicTrig, RightUltrasonicEcho)

        print(f"Left: {lDis:.1f} in | Right: {rDis:.1f} in")

        # Left vibrator: turn on if object detected, off otherwise
        if lDis < DISTANCE_THRESHOLD:
            GPIO.output(LeftVibrator, 1)
        else:
            GPIO.output(LeftVibrator, 0)

        # Right vibrator: turn on if object detected, off otherwise
        if rDis < DISTANCE_THRESHOLD:
            GPIO.output(RightVibrator, 1)
        else:
            GPIO.output(RightVibrator, 0)

        time.sleep(0.1)  # Avoid reading too fast to reduce sensor noise

def destroy():
    GPIO.output(LeftVibrator, 0)   # Turn off vibrators before cleanup
    GPIO.output(RightVibrator, 0)
    GPIO.cleanup()

if __name__ == "__main__":
    setup()
    try:
        loop()
    except KeyboardInterrupt:
        destroy()
