VENV     ?= .venv
PYTHON   ?= $(VENV)/bin/python
CAPTURE  ?= $(PYTHON) ppk2_capture.py $(if $(PORT),--port $(PORT))
# Serial port of the PPK2 (default: auto-detect)
PORT     ?=
# Measurement parameters: no defaults on purpose, every measurement must state them explicitly
VOLTAGE   ?=
DURATION  ?=
FREQUENCY ?=
NAME      ?=
POWER_ON_DELAY ?=
SETTLE    ?=

# $(call require,VAR,example): abort if VAR is not set on the command line
require = $(if $($(1)),,$(error $(1) is required, e.g. make $@ $(1)=$(2)  (see make help)))
MEASURE_ARGS = -v $(VOLTAGE) -d $(DURATION) -f $(FREQUENCY) $(if $(NAME),-n '$(NAME)')

.PHONY: help install check-ppk measure-voltage measure-ampere power-on summary clean

# ==============================================================================
# TARGETS
# ==============================================================================

help: ## Show this help message
	@echo "Makefile targets for the Nordic Power Profiler Kit II (PPK2)"
	@echo "Usage: make [target] [VAR=value ...]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  %-20s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""
	@echo "Parameters:"
	@echo "  VOLTAGE=mV        required  800-5000 (integer)"
	@echo "  DURATION=seconds  required  seconds, decimals allowed (e.g. 2.5); 0 = until Ctrl+C / SIGTERM"
	@echo "  FREQUENCY=Hz      required  1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000"
	@echo "  NAME=string       optional  label in the file name, letters/digits/._- only"
	@echo "  POWER_ON_DELAY=s  optional  DUT starts OFF, switched ON s seconds into the recording (< DURATION)."
	@echo "  SETTLE=s          optional  DUT switched ON, runs s seconds UNRECORDED, then recording starts."
	@echo "  PORT=/dev/ttyACMx optional  PPK2 serial port (default: auto-detect)"
	@echo ""
	@echo "Examples:"
	@echo "  make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000 NAME=idle"
	@echo "  make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000 SETTLE=2 NAME=settled"
	@echo "  make measure-voltage VOLTAGE=3300 DURATION=5 FREQUENCY=10000 POWER_ON_DELAY=1 NAME=boot"
	@echo "  make measure-ampere  VOLTAGE=1800 DURATION=60 FREQUENCY=100 NAME=current"
	@echo "  make power-on VOLTAGE=3300     (bench supply until Ctrl+C)"

install: ## Create the Python venv and install requirements (ppk2-api)
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt

check-ppk: ## Check the attached PPK2 (USB 1915:c00a) and list its port(s)
	@lsusb | grep -i '1915:c00a' || { echo "Error: no PPK2 (1915:c00a) on the USB bus, check the cable / DATA port."; exit 1; }
	@test -x $(PYTHON) || { echo "Error: $(PYTHON) not found, run: make install"; exit 1; }
	@out=$$($(CAPTURE) list) || { echo "Error: PPK2 is on USB but has no serial port (/dev/ttyACM*), try replugging it."; exit 1; }; \
	echo "$$out"; \
	for p in $$(echo "$$out" | cut -f1); do \
		test -r $$p -a -w $$p || { \
			echo "Error: no read/write access to $$p (owned by $$(stat -c %U:%G $$p))."; \
			echo "Add your user to the dialout group, then log out and back in:"; \
			echo "  sudo usermod -aG dialout $$USER"; \
			exit 1; }; \
	done

measure-voltage: ## Source meter: PPK2 POWERS the DUT at VOLTAGE DURATION FREQUENCY [SETTLE] [POWER_ON_DELAY] [NAME]
	$(call require,VOLTAGE,3300)$(call require,DURATION,10)$(call require,FREQUENCY,1000)
	$(if $(and $(SETTLE),$(POWER_ON_DELAY)),$(error give either SETTLE or POWER_ON_DELAY, not both))
	@$(CAPTURE) measure -m source $(MEASURE_ARGS) $(if $(SETTLE),--settle $(SETTLE)) $(if $(POWER_ON_DELAY),--power-on-delay $(POWER_ON_DELAY))

measure-ampere: ## Ampere meter: PPK2 in series, DUT has its own supply VOLTAGE DURATION FREQUENCY [NAME]
	$(call require,VOLTAGE,3300)$(call require,DURATION,10)$(call require,FREQUENCY,1000)
	$(if $(SETTLE)$(POWER_ON_DELAY),$(error SETTLE/POWER_ON_DELAY only work with measure-voltage, in ampere mode the PPK2 does not power the DUT))
	@$(CAPTURE) measure -m ampere $(MEASURE_ARGS)

power-on: ## power the DUT at VOLTAGE until Ctrl+C
	$(call require,VOLTAGE,3300)
	@$(CAPTURE) power-on -v $(VOLTAGE)

summary: ## Show the JSON summary of the latest measurement [NAME]
	@files=$$(ls -1 measurements/$(if $(NAME),*_$(NAME).json measurements/$(NAME).json,*.json) 2>/dev/null | sort); \
	test -n "$$files" || { echo "Error: no measurement$(if $(NAME), named '$(NAME)') in measurements/"; exit 1; }; \
	n=$$(echo "$$files" | wc -l); f=$$(echo "$$files" | tail -1); \
	test $$n -gt 1 && [ -n "$(NAME)" ] && echo "($$n measurements named '$(NAME)', showing the latest: $$f)" >&2; \
	cat "$$f"

clean: ## Delete ALL measurements in measurements/
	@n=$$(ls measurements/*.csv 2>/dev/null | wc -l); \
	find measurements -mindepth 1 ! -name .gitkeep -delete; rm -rf __pycache__; \
	echo "Removed $$n measurement(s) from measurements/"
