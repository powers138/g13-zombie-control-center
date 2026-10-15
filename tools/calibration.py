def calibrate(device, samples=100):

    print("Calibrating joystick...")
    print("Don't touch the stick.")

    total_x = 0
    total_y = 0

    for _ in range(samples):

        data = device.read(8)

        total_x += data[1]
        total_y += data[2]

    center_x = total_x // samples
    center_y = total_y // samples

    print(f"Center = ({center_x}, {center_y})")

    return center_x, center_y
