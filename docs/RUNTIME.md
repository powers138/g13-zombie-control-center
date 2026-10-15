# G13 Zombie Control Center Runtime

The production runtime uses a rootless Distrobox container.

Container name:

    zombiecc

Base image:

    quay.io/fedora/fedora:44

Fedora runtime packages are listed in:

    container-packages.txt

Python packages installed into the application virtual environment are listed in:

    requirements-runtime.txt

The Python virtual environment must be created with:

    python3 -m venv --system-site-packages <venv>

Zombie runtime processes must use:

    PYTHONNOUSERSITE=1

This allows the runtime to use Fedora-provided Python modules such as
python3-evdev and python3-gobject while preventing packages from the
user's ~/.local Python installation from leaking into the application.

The application virtual environment owns only the packages listed in
requirements-runtime.txt. In particular, evdev is supplied by Fedora
through python3-evdev and must not be installed into the virtual
environment.

A compiler toolchain is not required for the production runtime.
gcc and python3-devel are intentionally excluded.

The Distrobox container is disposable application infrastructure.
User settings, profiles, logs, and other persistent data live on the
host in the Zombie XDG directories and must survive container rebuilds.

Fedora 44 ships HIDAPI 0.15. The driver accounts for its changed
interrupted-read behavior during an intentional shutdown while still
allowing unexpected HID errors to propagate normally.
