FROM python:3.12-slim
WORKDIR /app
COPY jobradar ./jobradar
COPY config ./config
# Pure standard library: no pip install needed.
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "while true; do python -m jobradar; sleep ${INTERVAL_SECONDS:-900}; done"]
