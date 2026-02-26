# ==============================
# Kernel Module Makefile
# ==============================

# ------------------------------
# Kernel Module Configuration
# ------------------------------
obj-m += module/IPC_monitor.o
obj-m += module/runtime_monitor.o

KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

# Add include directory (EXTRA_CFLAGS applies to all modules)
EXTRA_CFLAGS := -I$(src)/include

# ==============================
# Build Targets
# ==============================

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

insmod:
	sudo insmod module/IPC_monitor.ko
	sudo insmod module/runtime_monitor.ko
	sudo chmod 666 /dev/runtime_monitor
	sudo chmod 666 /dev/IPC_monitor

rmmod:
	sudo rmmod runtime_monitor
	sudo rmmod IPC_monitor

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean