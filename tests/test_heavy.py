# test_heavy.py
import torch
import cv2

def do_work():
    # Static-only “heavy” classification comes from imports above
    # (torch=40, cv2=20 => heavy tier)
    s = 0
    for i in range(1000):
        for j in range(300):
            s += (i * j) % 7
    return s

if __name__ == "__main__":
    print(do_work())