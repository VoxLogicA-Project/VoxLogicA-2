FROM ubuntu:24.04

RUN echo "root:root" | chpasswd
RUN apt update
RUN apt -y dist-upgrade
RUN apt -y install software-properties-common
RUN apt update

RUN apt -y install git git-lfs htop screen unzip psmisc nvtop wget libgl-dev libegl-dev libfreetype-dev libglib2.0-0 ca-certificates curl gnupg python3 python3-pip python3-venv
