# CANARY FIXTURE: deliberately non-compliant Dockerfile (analyzer self-test).
FROM python:latest
RUN apt-get update && apt-get install -y curl
ADD backend/ /app/
WORKDIR /app
CMD python app.py
