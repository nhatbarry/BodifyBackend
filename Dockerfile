# psycopg2-binary and pillow both ship prebuilt manylinux wheels for this
# combination, so no compiler / libpq-dev / libjpeg-dev is needed - slim stays
# slim. If a future dependency needs a source build, add build-essential here
# rather than switching the base image.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app

# Koyeb (and most PaaS hosts) assign the port at runtime via $PORT rather than
# letting the image fix one - the shell form lets that env var expand.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
