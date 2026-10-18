# The image of the zero-install try: the π-λ tutorial (docs/tutorial-pi-lambda.md) in a dev container or
# GitHub Codespace. What it can and cannot do: docs/tutorial-pi-lambda.md, "Try it in your browser".
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# The lts-2026.10 stack (Lean v4.35.0 with Mathlib v4.35.0) and a released lean-pkg.
ARG LEAN_TOOLCHAIN=leanprover/lean4:v4.35.0
ARG LEAN_PKG_REF=v0.12.0

ENV DEBIAN_FRONTEND=noninteractive
# bubblewrap: the comparator's sandbox (`lake comparator` runs its judge in bwrap).
# systemd + dbus-user-session: the user manager that `lean-pkg receipt systemd` starts its unit under.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bubblewrap ca-certificates curl dbus dbus-user-session git pipx python3 python3-venv \
      systemd systemd-sysv zstd \
 && rm -rf /var/lib/apt/lists/*

# systemd runs as PID 1 (devcontainer.json: overrideCommand false). Units a container has no use for are
# masked, and so is every unit that would write kernel settings: the container is privileged, so
# systemd-sysctl and systemd-binfmt would change the host's. Lingering starts the vscode user's systemd manager at
# boot, with no login.
RUN systemctl mask getty.target console-getty.service systemd-udevd.service systemd-udevd-kernel.socket \
      systemd-udevd-control.socket systemd-modules-load.service sys-kernel-debug.mount \
      systemd-firstboot.service systemd-resolved.service systemd-sysctl.service systemd-binfmt.service \
      proc-sys-fs-binfmt_misc.automount proc-sys-fs-binfmt_misc.mount systemd-timesyncd.service \
      systemd-hwdb-update.service systemd-remount-fs.service \
 && mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/vscode

# elan, lean-pkg (pipx) and the throwaway registry live outside the home directory, owned by vscode.
ENV ELAN_HOME=/opt/elan PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/opt/pipx/bin
ENV PATH=/opt/elan/bin:/opt/pipx/bin:${PATH}
RUN install -d -o vscode -g vscode /opt/elan /opt/pipx /var/lib/lean-pkg-trial

USER vscode
RUN curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
      | sh -s -- -y --no-modify-path --default-toolchain "${LEAN_TOOLCHAIN}" \
 && lean --version \
 && pipx install "git+https://github.com/lean-pkg/lean-pkg.git@${LEAN_PKG_REF}" \
 && pipx install "reuse[charset-normalizer]" \
 && lean-pkg --version

USER root
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
