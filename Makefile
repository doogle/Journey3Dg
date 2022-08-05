
.PHONY: all build sfx-src sfx-mpy ul-src ul-mpy ul-sfx-src ul-sfx-mpy
.PHONY: target-rmdir target-mkdir run run-src run-mpy run-sfx-src run-sfx-mpy
.PHONY: clean clean-cache

include project.mk
-include local.mk

SRC_DIR = src
BUILD_DIR = build
MPY_FILES = $(SRC_FILES:.py=.mpy)

TARGET_APP_DIR = /Games/$(APP_NAME)

UL_CACHE_DIR = .ul-cache

SRC_PATHS := $(addprefix $(SRC_DIR)/,$(SRC_FILES))
MPY_PATHS := $(addprefix $(BUILD_DIR)/,$(MPY_FILES))

UL_CACHE_SRC_PATHS := $(addprefix $(UL_CACHE_DIR)/,$(SRC_FILES))
UL_CACHE_MPY_PATHS := $(addprefix $(UL_CACHE_DIR)/,$(MPY_FILES))

SFX_SRC_PATH = $(BUILD_DIR)/sfx-src/$(APP_NAME).mpy
SFX_MPY_PATH = $(BUILD_DIR)/sfx-mpy/$(APP_NAME).mpy

TARGET_RUN_PY = $(BUILD_DIR)/.target-run.py

AMPY = ampy $(AMPY_ARGS)

all: build

$(BUILD_DIR)/%.mpy:	$(SRC_DIR)/%.py
	@mkdir -p $(@D)
	mpy-cross -O3 -march=armv6m -o $@ $<

$(UL_CACHE_DIR)/%.py:	$(SRC_DIR)/%.py
	@if $(AMPY) ls $(TARGET_APP_DIR) | grep -x $(TARGET_APP_DIR)/$(patsubst %.py,%.mpy,$(@F)) >/dev/null ; then \
		echo "Deleting $(patsubst %.py,%.mpy,$(@F)) on Thumby" ; \
		$(AMPY) rm $(TARGET_APP_DIR)/$(patsubst %.py,%.mpy,$(@F)) ; \
		if [ -e $(UL_CACHE_DIR)/$(patsubst %.py,%.mpy,$(@F)) ]; then \
			rm $(UL_CACHE_DIR)/$(patsubst %.py,%.mpy,$(@F)) ; \
		fi ; \
	fi
	$(AMPY) put $< $(TARGET_APP_DIR)/$(@F)
	@mkdir -p $(@D)
	@touch $@

$(UL_CACHE_DIR)/%.mpy:	$(BUILD_DIR)/%.mpy
	@if $(AMPY) ls $(TARGET_APP_DIR) | grep -x $(TARGET_APP_DIR)/$(patsubst %.mpy,%.py,$(@F)) >/dev/null ; then \
		echo "Deleting $(patsubst %.mpy,%.py,$(@F)) on Thumby" ; \
		$(AMPY) rm $(TARGET_APP_DIR)/$(patsubst %.mpy,%.py,$(@F)) ; \
		if [ -e $(UL_CACHE_DIR)/$(patsubst %.mpy,%.py,$(@F)) ]; then \
			rm $(UL_CACHE_DIR)/$(patsubst %.mpy,%.py,$(@F)) ; \
		fi ; \
	fi
	$(AMPY) put $< $(TARGET_APP_DIR)/$(@F)
	@mkdir -p $(@D)
	@touch $@

build: $(MPY_PATHS)

$(SFX_SRC_PATH):	sfx/sfx.py $(SRC_PATHS)
	@mkdir -p $(@D)
	mpy-cross -O3 -march=armv6m -o $@ sfx/sfx.py
	sfx/sfx-build.py $@ $(join $(SRC_PATHS),$(addprefix :$(TARGET_APP_DIR)/,$(SRC_FILES)))

$(SFX_MPY_PATH):	sfx/sfx.py $(MPY_PATHS)
	@mkdir -p $(@D)
	mpy-cross -O3 -march=armv6m -o $@ sfx/sfx.py
	sfx/sfx-build.py $@ $(join $(MPY_PATHS),$(addprefix :$(TARGET_APP_DIR)/,$(MPY_FILES)))

sfx-src:	$(SFX_SRC_PATH)
sfx-mpy:	$(SFX_MPY_PATH)

target-rmdir:	clean-cache
	@if $(AMPY) ls /Games | grep -x $(TARGET_APP_DIR) >/dev/null ; then \
		echo "Deleting $(TARGET_APP_DIR) on Thumby" ; \
		$(AMPY) rmdir $(TARGET_APP_DIR) ; \
	fi

target-mkdir:
	@if ! $(AMPY) ls /Games | grep -x $(TARGET_APP_DIR) >/dev/null ; then \
		echo "Creating $(TARGET_APP_DIR) on Thumby" ; \
		$(AMPY) mkdir $(TARGET_APP_DIR) ; \
	fi

ul-src:	target-mkdir $(UL_CACHE_SRC_PATHS)

ul-mpy:	target-mkdir $(UL_CACHE_MPY_PATHS)

ul-sfx-src:	sfx-src target-rmdir target-mkdir
	$(AMPY) put $(SFX_SRC_PATH) $(TARGET_APP_DIR)/$(APP_NAME).mpy

ul-sfx-mpy:	sfx-mpy target-rmdir target-mkdir
	$(AMPY) put $(SFX_MPY_PATH) $(TARGET_APP_DIR)/$(APP_NAME).mpy

$(TARGET_RUN_PY):
	echo 'import Games.$(APP_NAME).$(APP_NAME)' >$@

run:	$(TARGET_RUN_PY)
	$(AMPY) run $(TARGET_RUN_PY)

run-src:	ul-src $(TARGET_RUN_PY)
	$(AMPY) run $(TARGET_RUN_PY)
run-mpy:	ul-mpy $(TARGET_RUN_PY)
	$(AMPY) run $(TARGET_RUN_PY)
run-sfx-src:	ul-sfx-src $(TARGET_RUN_PY)
	$(AMPY) run $(TARGET_RUN_PY)
run-sfx-mpy:	ul-sfx-mpy $(TARGET_RUN_PY)
	$(AMPY) run $(TARGET_RUN_PY)

clean:
	@echo Cleaning build output
	-rm -rf $(BUILD_DIR)
clean-cache:
	@echo Clearing upload cache
	-@rm -rf $(UL_CACHE_DIR)
