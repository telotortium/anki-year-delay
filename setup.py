from setuptools import setup, find_packages

setup(
    name="anki-year-delay",
    version="0.1.0",
    author="Robert Irelan",
    author_email="rirelan@gmail.com",
    description="Reschedules Anki notes with a certain tag into the future.",
    packages=find_packages(),
    install_requires=[
        "requests",
    ],
    entry_points={
        "console_scripts": [
            "anki-year-delay=anki_year_delay.__init__:main",
        ],
    },
)
