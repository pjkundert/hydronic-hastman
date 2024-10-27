from setuptools import setup
import os, sys

here = os.path.abspath( os.path.dirname( __file__ ))

__version__			= None
__version_info__		= None
exec( open( 'version.py', 'r' ).read() )

install_requires		= open( os.path.join( here, "requirements.txt" )).readlines()

setup(
    name			= "hydronic-hastman",
    version			= __version__,
    tests_require		= [ "pytest" ],
    install_requires		= install_requires,
    packages			= [ 
        "hydronic_hastman",
    ],
    package_dir			= {
        "hydronic_hastman":	".",
    },
    include_package_data	= True,
    author			= "Perry Kundert",
    author_email		= "perry@dominionrn.com",
    description			= "Hydronic Simulator",
    long_description		= """\
Modelling Darcy's Battery Cabinet.
""",
    license			= "Dual License; GPLv3 and Proprietary",
    keywords			= "hydronic Fanger comfort heat flow",
    url				= "https://github.com/pjkundert/hydronic-greenpro",
    classifiers			= [
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "License :: Other/Proprietary License",
        "Programming Language :: Python :: 2.7",
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Environment :: Console",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
