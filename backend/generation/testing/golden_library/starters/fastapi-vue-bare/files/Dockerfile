FROM python:3.13-slim@sha256:3333333333333333333333333333333333333333333333333333333333333333
WORKDIR /app
COPY . .
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
ENV PORT=8000
EXPOSE 8000
CMD ["python", "main.py"]
