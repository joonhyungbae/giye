# Offline demo. The default command runs the synthetic field and prints a summary.
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

CMD ["giye", "demo"]
