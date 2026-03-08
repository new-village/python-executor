FROM python:3.11-slim

ENV PYTHONUNBUFFERED=True
ENV APP_HOME=/app
WORKDIR $APP_HOME
COPY . ./

# Install dependencies if present
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Execute the module dynamically.
# By setting ENTRYPOINT to ["python", "-m"], we can specify the target module as a container argument.
ENTRYPOINT ["python", "-m"]

# Default module to execute if no arguments are provided.
CMD ["tasks.hello"]
