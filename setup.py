import os
from setuptools import setup
from setuptools import find_packages


def read(rel_path):
    here = os.path.abspath(os.path.dirname(__file__))
    with open(os.path.join(here, rel_path), 'r') as fp:
        return fp.read()


def get_version(rel_file='plugin.yaml'):
    lines = read(rel_file)
    for line in lines.splitlines():
        if 'package_version' in line:
            split_line = line.split(':')
            line_no_space = split_line[-1].replace(' ', '')
            line_no_quotes = line_no_space.replace('\'', '')
            return line_no_quotes.strip('\n')
    raise RuntimeError('Unable to find version string.')


setup(
    name='cloudify-chook-plugin',
    version=get_version(),
    description='Cloudify hook plugin where you check if another plugin '
                'is installed and forward the requst data to that plugin '
                'via calling its workflows',
    packages=find_packages(exclude=['tests*']),
    license='LICENSE',
    zip_safe=False,
    install_requires=[
        "cloudify-common>=4.4.0",
    ],
)
