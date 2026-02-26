# userlevel/c/pybind/Makefile
PYTHON ?= python3

CXX ?= g++
CXXFLAGS += -O3 -std=c++20 -fPIC

# Core configuration
LOGICAL_CORE_NUM ?= 16
PHYSICAL_CORE_NUM ?= 8

# Build modes
DEBUG ?= 0
TIME ?= 0

# Add preprocessor definitions
CXXFLAGS += -DLOGICAL_CORE_NUM=$(LOGICAL_CORE_NUM)
CXXFLAGS += -DPHYSICAL_CORE_NUM=$(PHYSICAL_CORE_NUM)

ifeq ($(DEBUG), 1)
    CXXFLAGS += -DDEBUG=1 -g
endif

ifeq ($(TIME), 1)
    CXXFLAGS += -DTIME=1
endif

# pybind11 + python include flags
PYBIND11_INCLUDES := $(shell $(PYTHON) -m pybind11 --includes)

# extension suffix (e.g., .cpython-310-x86_64-linux-gnu.so)
EXT_SUFFIX := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")

# paths (repo-relative)
ROOT := $(abspath ../../..)
SRC_DIR := $(ROOT)/userlevel/c
INC_DIR := $(SRC_DIR)/include
CPP_DIR := $(SRC_DIR)/src
BINDINGS := bindings.cpp
TARGET := smtcheck_native$(EXT_SUFFIX)

# output to python package dir so it can be imported
PY_PKG_DIR := $(ROOT)/userlevel/python/smtcheck

SRCS := $(BINDINGS) $(CPP_DIR)/job_mapper.cpp

all: $(PY_PKG_DIR)/$(TARGET)

$(PY_PKG_DIR)/$(TARGET): $(SRCS)
	$(CXX) $(CXXFLAGS) $(PYBIND11_INCLUDES) -I$(INC_DIR) \
		-shared -o $@ $^

clean:
	rm -f $(PY_PKG_DIR)/smtcheck_native*.so
