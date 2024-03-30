#!/usr/bin/env bash 

set -Eeuo pipefail

mkdir /usr/local/lib/k3ng_rotator
ln -s rpc_daemon.py /usr/local/lib/k3ng_rotator/rpc_daemon.py
ln -s k3ng_rotator.service /etc/systemd/user/k3ng_rotator.service

systemctl --user daemon-reload
systemctl --user restart python_demo_service
