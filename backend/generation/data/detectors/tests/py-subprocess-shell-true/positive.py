import subprocess


def ping(host):
    return subprocess.run("ping -c1 " + host, shell=True, check=False)
