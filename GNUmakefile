#
# GNU 'make' file
# 

# PY[3] is the target Python interpreter.  It must have pytest installed.

PY3	?= python3
PY3_V	= $(shell $(PY3) -c "import sys; print('-'.join((next(iter(filter(None,sys.executable.split('/')))),sys.platform,sys.implementation.cache_tag)))" 2>/dev/null )

VERSION=$(shell $(PY3) -c 'exec(open("version.py").read()); print( __version__ )')

# TARGET=... nix-shell  # CPython version targets: py2, py3{10,11,12,13}
export TARGET	?= py312

GHUB_NAME	= hydronic-hastman

# We'll agonizingly find the directory above this makefile's path
VENV_DIR	= $(abspath $(dir $(abspath $(lastword $(MAKEFILE_LIST))))/.. )
VENV_NAME	= $(GHUB_NAME)-$(VERSION)-$(PY3_V)
VENV		= $(VENV_DIR)/$(VENV_NAME)
VENV_OPTS	=

WHEEL		= dist/hydronic_hastman-$(VERSION)-py3-none-any.whl


# To see all pytest output, uncomment --capture=no
PYTESTOPTS=-vv --capture=no --log-cli-level=WARNING # INFO # 25 == NORMAL 23 == DETAIL


PY3TEST=TZ=$(TZ) $(PY3) -m pytest $(PYTESTOPTS)

.PHONY: all help analyze pylint test clean upload
all:			help

help:
	@echo "Targets:"
	@echo "  help			This help"
	@echo "  test			Run unit tests under Python2/3 (no serial_test w/o 'make SERIAL_TEST=1 test')"
	@echo "  install		Install in /usr/local for Python2/3"
	@echo "  clean			Remove build artifacts"
	@echo "  upload			Upload new version to pypi (package maintainer only)"
	@echo
	@echo "    virtualbox-*		Manage VirtualBox    virtual machine"
	@echo "    vmware-*		Manage VMWare Fusion virtual machine (recommended; requires license)"
	@echo "      -debian-...	Specify Debian Jessie  64-bit VM (VMware 6 compatible)"
	@echo "      -ubuntu-...	Specify Ubuntu Precise 64-bit VM (VMware 5 compatible)"
	@echo "       ...-up		Bring up the virtual machine, configuring if necessary"
	@echo "       ...-halt		Stop the virtual machine"
	@echo "       ...-destroy	Discard the configured virtual machine"
	@echo
	@echo "       ...-ssh		Establish SSH communications with the virtual machine"
	@echo 
	@echo "EXAMPLES"
	@echo "  vmware-debian-up	Brings up Jessie VM w/ Docker capability" 
	@echo "  vmware-debian-ssh	Log in to the VM" 

analyze:
	$(PY3) -m flake8 --color never -j 1 --max-line-length=250 \
	  --exclude lib,bin,dist,build,signals,.git \
	  --ignore=W503,E201,E202,E203,E127,E211,E221,E222,E223,E225,E226,E231,E241,E242,E251,E265,E272,E274,E291 \

pylint:
	pylint . --disable=W,C,R

#
# nix-...:
#
# Use a NixOS environment to execute the make target, eg.
#
#     nix-venv-activate
#
#     The default is the Python 312 TARGET in default.nix.  See default.nix for
# other Python version targets.
#

nix-%:
	nix-shell --pure --run "make $*"


#
# test...:	Perform Unit Tests
#
#     Assumes that the requirements.txt has been installed in the target Python environment.  This
# is probably best accomplished by first creating/activating a venv, and then running the test:
#
#     $ make nix-venv-activate
#     (crypto-licensing-4.0.0) [perry@Perrys-MBP crypto-licensing (feature-py-3.12)]$ make test
#     make[1]: Entering directory '/Users/perry/src/crypto-licensing'
#     ...
#
test:
	$(PY3TEST)

clean:
	@rm -rf MANIFEST *.png build dist auto *.egg-info $(shell find . -name '*.pyc' -o -name '__pycache__' )


build-check:
	@$(PY3) -m build --version \
	    || ( echo "\n*** Missing Python modules; run:\n\n        $(PY3) -m pip install --upgrade -r requirements-dev.txt\n" \
	        && false )

build:	build-check clean
	$(PY3) -m build
	@ls -last dist

$(WHEEL):	build

install:	$(WHEEL)
	$(PY3) -m pip install --force-reinstall $^

install-%:  # ...-dev, -tests
	$(PY3) -m pip install --upgrade -r requirements-$*.txt

# Run only tests with a prefix containing the target string, eg test-blah
test-%:
	$(PY_TEST) *$*_test.py
test2-%:
	$(PY2TEST) *$*_test.py
test3-%:
	$(PY3TEST) *$*_test.py
test23-%:
	$(PY2TEST) *$*_test.py
	$(PY3TEST) *$*_test.py

unit-%:
	$(PY_TEST) -k $*
unit2-%:
	$(PY2TEST) -k $*
unit3-%:
	$(PY3TEST) -k $*
unit23-%:
	$(PY2TEST) -k $*
	$(PY3TEST) -k $*


#
# venv:		Create a Virtual Env containing the installed repo
#
.PHONY: venv venv-activate.sh venv-activate
venv:			$(VENV)
venv-activate.sh:	$(VENV)/venv-activate.sh
venv-activate:		$(VENV)/venv-activate.sh
	@echo; echo "*** Activating $< VirtualEnv for Interactive $(SHELL)"
	@bash --init-file $< -i
# Create the venv, and then install cpppo from the current directory
$(VENV):
	@echo; echo "*** Building $@ VirtualEnv..."
	@rm -rf $@ && $(PY3) -m venv $(VENV_OPTS) $@ \
	    && source $@/bin/activate \
	    && make install-dev install

# Activate a given VirtualEnv, and go to its routeros_ssh installation
# o Creates a custom venv-activate.sh script in the venv, and uses it start
#   start a sub-shell in that venv, with a CWD in the contained routeros_ssh installation
$(VENV)/venv-activate.sh: $(VENV)
	( \
	    echo "PS1='[\u@\h \W)]\\$$ '";	\
	    echo "[ -r ~/.git-completion.bash ] && source ~/.git-completion.bash"; \
	    echo "[ -r ~/.git-prompt.sh ] && source ~/.git-prompt.sh && PS1='[\u@\h \W\$$(__git_ps1 \" (%s)\")]\\$$ '"; \
	    echo "source $</bin/activate";	\
	) > $@


#
# Target to allow the printing of 'make' variables, eg:
#
#     make print-CXXFLAGS
#
print-%:
	@echo $* = $($*)
	@echo $*\'s origin is $(origin $*)
