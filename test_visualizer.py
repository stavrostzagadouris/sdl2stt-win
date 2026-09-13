"""
Standalone test script to verify that the visualizer window appears on your screen.
Displays the visualizer for 4 seconds with a bouncing level meter.
"""
import sys
import time
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
from core.visualizer import VisualizerBar

print("Starting visualizer test...")
print("The visualizer bar should appear at the bottom-center of your screen right now.")

level = 0.0
v = VisualizerBar(lambda: level)
v.start()
v.show()

# Animate for 4 seconds
start = time.time()
while time.time() - start < 4.0:
    t = (time.time() - start) * 4
    # Bouncing sine wave
    import math
    level = abs(math.sin(t)) * 0.7
    time.sleep(0.03)

v.hide()
v.stop()
print("Test completed successfully! The visualizer should have appeared at bottom-center.")
