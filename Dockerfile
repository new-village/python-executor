# Use the official Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED True

# Copy local code to the container image.
ENV APP_HOME /app
WORKDIR $APP_HOME
COPY . ./

# Install production dependencies.
# If you add requirements later, uncomment the following line
# RUN pip install --no-cache-dir -r requirements.txt

# Run main.py on container startup.
ENTRYPOINT ["python", "main.py"]
