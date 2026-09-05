FROM debian:bookworm-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      git build-essential python3 python3-dev python3-venv libffi-dev ca-certificates pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/Klipper3d/klipper /klipper
WORKDIR /klipper
# "Linux process" MCU target builds with native gcc, no cross compiler needed.
RUN printf '%s\n' \
      'CONFIG_LOW_LEVEL_OPTIONS=y' \
      'CONFIG_MACH_LINUX=y' \
      'CONFIG_BOARD_DIRECTORY="linux"' \
      'CONFIG_CLOCK_FREQ=50000000' \
      'CONFIG_LINUX_SELECT=y' \
      'CONFIG_WANT_GPIO_BITBANGING=y' \
      'CONFIG_WANT_DISPLAYS=y' \
      'CONFIG_WANT_SENSORS=y' \
      'CONFIG_WANT_SOFTWARE_I2C=y' \
      'CONFIG_WANT_SOFTWARE_SPI=y' \
      > .config \
    && make olddefconfig \
    && make -j4 \
    && ls -l out/klipper.dict
RUN python3 -m venv /venv && /venv/bin/pip install -q --upgrade pip \
    && /venv/bin/pip install -q -r scripts/klippy-requirements.txt
RUN /venv/bin/python -c "import cffi, greenlet; print('klippy deps ok')"
