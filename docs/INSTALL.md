# G13 Zombie Control Center Installation

These instructions describe the production installation for
G13 Zombie Control Center 1.0.0.

For this release, the primary full-regression environment was:

- Bazzite 43 with KDE/Wayland and Steam Gaming Mode/Gamescope

Additional installation and runtime portability testing was completed on:

- Bazzite 44 with KDE/Wayland
- Linux Mint 22.3 with XFCE/X11
- Linux Mint 22.3 with Cinnamon/X11
- Debian 13 with Cinnamon/X11
- Fedora Workstation 44
- Fedora Silverblue 44 with GNOME/Wayland

The installer currently supports x86_64 Linux systems and uses a
rootless Distrobox/Podman runtime.

## Installation overview

Complete the installation in this order:

1. Check or install the required host tools.
2. Complete the SELinux prerequisite on Bazzite or Fedora.
3. Configure and verify device permissions.
4. Install the application runtime.
5. Install the application-menu entry.
6. Install and start the production driver for the current desktop.
7. Launch G13 Zombie Control Center.

Unless a section says otherwise, run the Zombie installation commands
from the extracted release directory as your normal user.

Do not skip the dry runs. They show what each installer intends to do
without making the corresponding changes.

## Before installing

The host must provide:

- Python 3
- Distrobox 1.8.1.2 or newer
- Podman
- crun
- systemd user services

Host Python 3 is used to run the installation and maintenance helpers.
The Zombie application runtime uses its own managed Python environment
inside the Fedora container.

The device-permission installer verifies Python 3, Distrobox, Podman,
and crun before making any host permission changes.

### Check installed prerequisites

Check the installed host tools before continuing:

    python3 --version
    distrobox --version
    podman --version
    crun --version
    systemctl --version | head -n 1

If a command reports `command not found`, install that prerequisite using
the instructions for the host distribution below.

Zombie requires Distrobox 1.8.1.2 or newer. The installer also verifies
that Podman is running as a local rootless container engine.

### Verify administrator access

The Zombie installers must be run as the normal user, not as root.
However, the normal user must be allowed to use `sudo` when host packages,
device permissions, or system configuration need to be changed.

Verify that the current account can use `sudo`:

    sudo -v

Enter the current user's password when prompted.

If the account is not permitted to use `sudo`, fix its administrator
access before continuing. Use an existing administrator or root account
and the host distribution's normal account-management procedure.

### Install missing prerequisites

Only use `sudo` with the package-management or system commands shown in
this guide. Do not prefix `install.sh` or any Zombie Python installer with
`sudo`.

#### Fedora Workstation

Install any missing prerequisites with:

    sudo dnf install python3 distrobox podman crun

Fedora 44 provides a Distrobox version newer than Zombie's minimum
requirement.

#### Fedora Silverblue

Podman and the container runtime are normally already available on
Silverblue. If a required package is missing, install the missing package
with rpm-ostree. For example:

    sudo rpm-ostree install distrobox

Reboot into the new deployment before continuing.

If another prerequisite is reported missing, install it the same way by
replacing `distrobox` in the command above with the name of the missing
package. Reboot into the new deployment afterward.

#### Debian 13

Install the required packages with:

    sudo apt update
    sudo apt install python3 distrobox podman crun

Debian 13 provides Distrobox 1.8.1.2, which meets Zombie's minimum
requirement.

#### Linux Mint 22.3

Install Python, Podman, crun, and curl with:

    sudo apt update
    sudo apt install python3 podman crun curl

Linux Mint 22.3's repository version of Distrobox is older than Zombie's
minimum requirement. Install a current Distrobox release using the
official Distrobox installer:

    curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh

Then verify:

    distrobox --version

#### APT package errors

If Debian or Linux Mint reports `Unable to locate package`, the system may
not have active network package sources. Check the distribution's software
repository configuration, run `sudo apt update`, and then retry the package
installation.

#### Bazzite

Distrobox and the required container tooling are normally included with
Bazzite. Check the installed versions using the commands above before
continuing.

If one of the required container tools is unexpectedly missing, update
Bazzite before attempting to layer additional system packages.

### Verify Podman

Verify that Podman is available as a local rootless engine:

    podman info --format '{{.Host.Security.Rootless}} {{.Host.ServiceIsRemote}}'

The expected result is:

    true false

Do not continue if Podman reports a rootful or remote engine.

An Internet connection is required during the initial installation so the
Fedora container image, Fedora packages, and Python dependencies can be
downloaded.

Do not run the Zombie installers with `sudo`. Installation is performed
for the current user.

## Bazzite/Fedora SELinux prerequisite

This section applies to Bazzite, Fedora Workstation, Fedora Silverblue,
and other SELinux-based Fedora systems. Debian and Linux Mint users can
skip this section.

The production Distrobox container must be allowed to access host devices.

Check the current setting:

    getsebool container_use_devices

If it reports:

    container_use_devices --> off

enable it with:

    sudo setsebool -P container_use_devices on

Verify the result:

    getsebool container_use_devices

The expected result is:

    container_use_devices --> on

A reboot is not required after changing this SELinux boolean.

## Device permissions

Zombie needs access to Linux input devices for the G13 driver and for
features such as physical modifier and mouse-state detection.

Connect the Logitech G13 before running the device-permission installer
so Zombie can detect the device and verify access to its HID interface.

Before running the application runtime installer, preview the required
host permission changes:

    python3 install_device_permissions.py --dry-run

The dry run does not change anything. It reports the current permission
state and lists any changes that would be required.

If the plan is correct, apply it:

    python3 install_device_permissions.py

Run the permission installer as your normal user, without `sudo`.
The installer requests elevated privileges itself only when a host
permission change is actually required.

The installer checks:

- Membership in the `input` group
- Availability of the `uinput` kernel module
- Write access to `/dev/uinput`
- Read/write access to detected Logitech G13 HID interfaces

Existing sufficient permissions are left unchanged.

On systems that need additional udev rules, Zombie installs narrow rules
for `/dev/uinput` and Logitech G13 devices only. It does not change
permissions for unrelated HID devices.

If the `uinput` kernel module is not loaded, the permission installer
loads it with `modprobe` and installs
`/etc/modules-load.d/zombiecc-uinput.conf` so it is loaded automatically
on future boots.

Module state is checked independently of `/dev/uinput`, because some
systems can provide a static device node before the kernel module is
active.

If the current user must be added to the `input` group, a full reboot is
recommended before continuing so the new supplementary group is active.

A logout/login may work on some desktops, but some session managers can
retain stale session state. If `id` does not show the `input` group after
logging back in, reboot before continuing.

### Verify device permissions

If the permission installer changed `input` group membership, reboot
before continuing.

Then return to the extracted Zombie release directory and verify the
permissions:

    python3 install_device_permissions.py --dry-run

A fully configured system should report that:

- the `input` group is active for the current session
- `/dev/uinput` is writable
- the detected G13 hidraw device is read/write accessible
- no additional permission changes are required

Typical successful output includes lines similar to:

    input group:  active for current session
    uinput:       /dev/uinput writable
    G13 hidraw:   /dev/hidrawN read/write accessible
    Permissions are already sufficient. No changes required.

The `/dev/hidrawN` number can vary between systems and can change between
boots. Do not rely on a specific hidraw number when troubleshooting.

If the installer still reports that the user must be added to the
`input` group after a reboot, do not continue with the driver installation.
Resolve the group-membership problem first.

### Fedora Silverblue input-group setup

On Fedora Silverblue and other Fedora Atomic systems, the `input` group
may be provided by the immutable system group database without an
existing writable `/etc/group` entry.

G13 Zombie Control Center's device-permission installer handles this
automatically when the current user must be added to the `input` group.

If the installer changes group membership, reboot before continuing and
then run the permission dry run again to verify that the new group
membership is active.

### Bazzite input-group notes

On Bazzite, run the Zombie device-permission installer first. The existing
system permission policy may already provide the required access, in which
case the installer reports that no changes are required.

If the current user must be added to the `input` group, Bazzite provides
its own supported method for making that change.

The preferred graphical method is available through Bazzite Portal.

The terminal alternative is:

    ujust add-user-to-input-group add

After the group membership is changed, reboot before continuing.

Then verify the result with:

    python3 install_device_permissions.py --dry-run

Do not hardcode `/dev/input/eventN` or `/dev/hidrawN` numbers when
troubleshooting. Linux device numbers can change between boots.

## Install the application runtime

Extract the G13 Zombie Control Center release into a normal user-writable
directory.

Open a terminal in the extracted release directory. If necessary, use
`cd` to enter it:

    cd /path/to/extracted-release

Unless a later instruction says otherwise, run the remaining installation
commands from that directory.

Preview the installation first:

    ./install.sh --dry-run

The dry run checks the host, release files, container configuration,
host input-device access, dependencies, runtime libraries, source syntax,
and factory configuration without creating the production container or
installing Zombie.

The `install.sh --dry-run` check does not test whether the G13 itself is
present or accessible. G13 device access is checked separately by the
device-permission installer.

Review the plan before continuing. If the dry run reports an error,
resolve that problem before running the actual installation.

Install the application:

    ./install.sh

The installer performs six stages. On the first installation, there can
be a noticeable pause after:

    [2/6] Preparing the production container (details in log).

before this appears:

    [3/6] Initializing Distrobox and installing Fedora runtime packages.

This pause is normal while Distrobox and Podman create and initialize the
new Fedora container. The detailed container output is written to the
installer log, so the terminal may show little or no new output during
this part. Do not interrupt the installer solely because the output
pauses between stages 2 and 3.

Stage 3 can also take a while while Fedora packages are downloaded and
installed. Keep the terminal open until the installer reports completion
or displays an error.

The installer creates the production `zombiecc` Distrobox container,
installs the application runtime, creates the managed Python environment,
and installs the host launcher.

The application is installed under:

    ~/.local/share/zombiecc/app

The normal launcher is installed at:

    ~/.local/bin/zombiecc

Installer logs are stored under:

    ~/.local/state/zombiecc/logs

At completion, `install.sh` reports:

    Stage 1 completed.

It also prints the installed launcher path and the installer log path.

If `install.sh` fails, read the error shown in the terminal and inspect
the log path printed by the installer before retrying.

This installation step does not start the editor or the G13 driver.

## Install the application-menu entry

Preview the desktop integration:

    python3 install_desktop.py --dry-run

The dry run shows the desktop entry that would be installed without
writing it.

If the plan is correct, install the application-menu entry:

    python3 install_desktop.py

G13 Zombie Control Center should then appear in the desktop application
menu.

Some desktop environments, particularly Cinnamon, may not refresh the
application menu immediately after the entry is installed.

If Zombie launches successfully from:

    ~/.local/bin/zombiecc

but the application-menu entry is not visible, fully log out and back in
or reboot before treating the missing menu entry as an installation
problem.

To also create a shortcut on the Desktop:

    python3 install_desktop.py --desktop-shortcut

The desktop integration uses the installed Zombie launcher rather than
the files in the release directory.

## Install the driver service

Preview the production driver-service installation:

    python3 install_driver_service.py --dry-run

The dry run shows the service and session-integration files that would be
installed without writing them or starting the driver.

If the plan is correct, install them:

    python3 install_driver_service.py

The service installer deliberately leaves the production driver stopped
and disabled. How the driver is started depends on the desktop
environment.

### Cinnamon and XFCE

On Cinnamon and XFCE, Zombie installs a desktop-session bridge that starts
the production driver when the desktop session begins and stops it when
the session ends.

Do not enable the service manually on Cinnamon or XFCE.

After installing the service, reboot. A full reboot is preferred because
it starts a clean desktop session and avoids stale session state.

After logging back in, verify the service:

    systemctl --user show zombie-control-center-driver.service \
        --property=ActiveState,SubState,UnitFileState \
        --no-pager

A healthy Cinnamon or XFCE session should report:

    ActiveState=active
    SubState=running
    UnitFileState=disabled

`disabled` is expected on Cinnamon and XFCE. The session bridge, rather
than systemd enablement, controls the driver lifecycle.

### KDE/Plasma and GNOME

On KDE/Plasma and GNOME, enable and start the production driver:

    systemctl --user enable --now zombie-control-center-driver.service

Then verify it:

    systemctl --user show zombie-control-center-driver.service \
        --property=ActiveState,SubState,UnitFileState \
        --no-pager

A healthy enabled service should report:

    ActiveState=active
    SubState=running
    UnitFileState=enabled

Only one G13 driver should control the device at a time.

## Launch G13 Zombie Control Center

Launch G13 Zombie Control Center normally from the desktop application
menu.

It can also be started from a terminal with:

    ~/.local/bin/zombiecc

The first launch after boot may take longer than later launches while the
production container is started.

The editor does not need to remain open for normal G13 operation. The G13
driver runs independently after it has been configured for the desktop as
described above.

## GNOME/Wayland application switching

On GNOME/Wayland, `Detect Active App` and automatic application-profile
switching require the Focused Window D-Bus GNOME Shell extension.

Install and enable `Focused Window D-Bus` through GNOME Extensions.

If the GNOME Extensions website downloads the extension but does not
install it automatically, extract the downloaded extension and copy its
contents to:

    ~/.local/share/gnome-shell/extensions/focused-window-dbus@flexagoon.com/

The extension directory should contain files such as `extension.js` and
`metadata.json` directly inside it.

Fully log out of GNOME and log back in, then enable the extension:

    gnome-extensions enable focused-window-dbus@flexagoon.com

Verify that the extension is installed:

    gnome-extensions list | grep focused-window-dbus

The installed extension should appear as:

    focused-window-dbus@flexagoon.com

Verify that it is active:

    gnome-extensions info focused-window-dbus@flexagoon.com

The output should report:

    Enabled: Yes
    State: ACTIVE

Zombie can run without this extension, but GNOME/Wayland active-window
detection and automatic application-profile switching will not work.

## Add Zombie to Steam Gaming Mode

Install Zombie normally before adding it to Steam.

In Steam Desktop Mode:

1. Open Steam.
2. Select `Games`.
3. Select `Add a Non-Steam Game to My Library`.
4. Choose `Browse`.
5. Navigate to `~/.local/bin/`.
6. Select `zombiecc`.
7. Add it to the Steam library.

Rename the Steam shortcut to:

`G13 Zombie Control Center`

Do not force Proton or another Steam Play compatibility tool for the
shortcut. Zombie is a native Linux application.

After adding the shortcut, return to Gaming Mode. Zombie can then be
launched from the Non-Steam section of the Steam library.

### Gaming Mode display size

In Steam Gaming Mode, the Zombie editor uses a fixed presentation size
and cannot be resized like a normal desktop window.

If the editor appears too large or too small for the display, adjust the
Steam/Gamescope display scaling or game-resolution settings rather than
trying to resize the Zombie editor itself.

## Profiles and user data

Zombie stores user data separately from the installed application.

Profiles are stored under:

`~/.local/share/zombiecc/profiles`

Configuration is stored under:

`~/.config/zombiecc`

Logs, diagnostics, update backups, and other application state are
stored under:

`~/.local/state/zombiecc`

The production Distrobox container is application infrastructure.
Profiles and settings are not stored inside the container.

This separation allows the application or its container to be replaced
without deleting normal user data.

Back up the profile and configuration directories before making manual
changes to them.

## Updating Zombie

Download or extract the new release into a directory separate from the
installed application directory.

Open a terminal in the new extracted release directory. Run the updater
from the new release, not from:

    ~/.local/share/zombiecc/app

The updater rejects release files with unsafe permission bits, including
files that are writable by the group or by other users. If an extracted
release reports:

    Unsafe permissions on file: ...

remove group/world write permission from the extracted regular files:

    find /path/to/extracted-release -type f -exec chmod go-w {} +

Do not change every file to mode `644`. Executable files such as
`zombiecc` must retain their executable permission.

Close the installed Zombie editor before updating.

Stop the production driver:

    systemctl --user stop zombie-control-center-driver.service

Preview the update:

    python3 update_app.py --dry-run

The updater displays the installed and incoming versions along with the
files that will be changed. Review the entire plan before continuing.

If the plan is correct, apply the update:

    python3 update_app.py

After the update completes, restart the driver:

    systemctl --user start zombie-control-center-driver.service

Starting the service manually after an update does not enable it. On
Cinnamon and XFCE, the service should remain disabled and will return to
normal session-bridge management at the next login.

Verify the running service:

    systemctl --user show zombie-control-center-driver.service \
        --property=ActiveState,SubState,UnitFileState,Result \
        --no-pager

The service should report `ActiveState=active`, `SubState=running`, and
`Result=success`. `UnitFileState` should match the desktop-specific setup
described earlier in this guide.

The updater verifies the installed application before changing it and
stores rollback information under:

`~/.local/state/zombiecc/updates`

Profiles, settings, the managed Python environment, desktop entries,
driver-session integration, and the driver-service configuration are not
replaced by a normal code update.

### Roll back an update

Close the editor and stop the production driver before performing a
rollback.

Updater backups are stored under:

`~/.local/state/zombiecc/updates`

List the available backups:

    ls -1 ~/.local/state/zombiecc/updates

Run the rollback using the absolute path to the appropriate backup:

    python3 update_app.py \
        --rollback /absolute/path/to/update-backup

After the rollback completes, restart the driver:

    systemctl --user start zombie-control-center-driver.service

Verify the service using the command in the previous section.

### Recover an interrupted update

If an update was interrupted and Zombie reports an unfinished update
journal, keep the editor and driver stopped.

Run:

    python3 update_app.py --recover

The updater will restore the original verified application payload.

After recovery completes, restart the driver:

    systemctl --user start zombie-control-center-driver.service

Verify the service using the command in the update section.

## Diagnostics

Diagnostic and hardware-test tools are available under:

`Settings > Developer`

The available tools include:

- Test LCD
- Test LEDs
- Verify onboard writes
- Generate Diagnostic Report

Diagnostic reports are stored under:

`~/.local/state/zombiecc/diagnostics`

A diagnostic report contains information useful for troubleshooting,
including:

- Zombie version
- Python version
- Qt version
- Qt platform
- Desktop/session information
- Driver connection state
- Active runtime profile
- Runtime profile source
- Active window class
- Zombie application and data paths

Diagnostic reports do not include profile contents, macros, application
rules, or settings values.

Generate a diagnostic report before asking for help with a problem when
the editor is still able to open.

## Driver troubleshooting

Check the current service state:

    systemctl --user status zombie-control-center-driver.service --no-pager

On Cinnamon and XFCE, an active service with `UnitFileState=disabled` is
normal. On KDE/Plasma and GNOME, the service should normally be enabled.

View the production driver's log from the current boot:

    journalctl --user \
        -u zombie-control-center-driver.service \
        -b \
        --no-pager

To show only the most recent messages:

    journalctl --user \
        -u zombie-control-center-driver.service \
        -b \
        -n 100 \
        --no-pager

If the driver is not running, verify that the G13 is connected and rerun
the permission check from the extracted release directory:

    python3 install_device_permissions.py --dry-run

If physical keyboard or mouse state is not detected, verify that the
current user belongs to the `input` group:

    id

If the group was added recently, reboot before testing again.

Do not hardcode `/dev/input/eventN` or `/dev/hidrawN` device numbers when
troubleshooting. Those numbers can change between boots or reconnects.

If the editor opens but the application-menu entry is missing on
Cinnamon, log out and back in or reboot to refresh the desktop session.

## Uninstalling Zombie

Run the uninstaller from a G13 Zombie Control Center source or release
directory.

Close the Zombie editor before uninstalling.

Preview the uninstall first:

    python3 uninstall.py --dry-run

Review the entire plan before continuing.

Uninstall Zombie:

    python3 uninstall.py

The normal uninstall removes the Zombie-managed:

- Production application
- Host launcher
- Application-menu entry
- Desktop shortcut, if installed
- Production driver service and session integration
- Production `zombiecc` Distrobox container

Normal uninstall preserves the user's:

- Profiles
- Configuration
- Logs
- Diagnostics
- Update backups
- Other persistent Zombie state

The preserved data remains under:

`~/.local/share/zombiecc/profiles`

`~/.config/zombiecc`

`~/.local/state/zombiecc`

### Completely remove user data

To uninstall Zombie and also delete its persistent profiles,
configuration, logs, diagnostics, backups, and other user data:

    python3 uninstall.py --purge-user-data

This permanently deletes the preserved Zombie user data. Use this option
only when that data is no longer wanted.

### What uninstall does not change

Uninstalling Zombie does not modify G13 onboard memory.

It also does not remove Steam Non-Steam shortcuts. Remove the
G13 Zombie Control Center shortcut from Steam separately if desired.

Device rules and group membership are not removed automatically.

## Production runtime details

Technical information about Zombie's Fedora container, Python environment,
and production runtime is documented in:

`docs/RUNTIME.md`

Development and troubleshooting notes are kept separate from the
end-user installation instructions.

## Logitech notice

G13 and Logitech are trademarks or product names of Logitech.

G13 Zombie Control Center is an independent project and is not affiliated
with or endorsed by Logitech.
