import subprocess


def ping(host):
    return subprocess.run(["ping", "-c1", host], shell=False, check=False)
