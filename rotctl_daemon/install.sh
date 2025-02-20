#!/usr/bin/env bash 
# TODO: Make this a system service

set -Eeuo pipefail

# yeah, I know. dunno how to do this safely to run in a service though. 
sudo pip3 install k3ng-rotator-python --break-system-packages

id -u k3ng_rotator &>/dev/null || sudo useradd -r -s /bin/false k3ng_rotator

sudo cp -f rotctld.service /etc/systemd/system/rotctld.service
sudo chown root:root /etc/systemd/system/rotctld.service
sudo chmod 644 /etc/systemd/system/rotctld.service

sudo systemctl daemon-reload
sudo systemctl enable rotctld 
sudo systemctl restart rotctld

printf "\nService installed!\n"
