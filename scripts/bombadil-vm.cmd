@echo off
rem Bombadil in a VM window on Windows: QEMU with KVM inside a WSL distro named "bombadil", shown
rem through WSLg. Double-click it, or run it with the same arguments as scripts/wsl-vm.sh:
rem   bombadil-vm            boot the installed VM (the first run builds the ISO and installs it)
rem   bombadil-vm live       boot the live ISO
rem   bombadil-vm refresh    keep the VM's disk (login, apps, files): restore point first, then move it to this checkout
rem   bombadil-vm stop       shut the VM down cleanly
rem   bombadil-vm reinstall  rebuild if this checkout moved on, wipe the VM's disk (asks first), install again
rem   bombadil-vm build      only build the ISO
rem Remove everything with: wsl --unregister bombadil
wsl -d bombadil -u root -- true >nul 2>&1 || (
  echo Setting up the "bombadil" WSL distro, once...
  wsl --install archlinux --name bombadil --no-launch || goto :failed
)
wsl -d bombadil -u root --cd "%~dp0.." -- bash scripts/wsl-vm.sh %* || goto :failed
exit /b 0
:failed
echo.
echo Bombadil VM failed; the messages above say where.
pause
exit /b 1
