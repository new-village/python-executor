FROM python:3.11-slim

ENV PYTHONUNBUFFERED=True
ENV APP_HOME=/app
WORKDIR $APP_HOME
COPY . ./

# Set default task module if not provided
ENV TASK_MODULE="tasks.hello"

# Execute the module dynamically. 
# We use 'sh -c' to expand the $TASK_MODULE environment variable.
ENTRYPOINT ["sh", "-c", "python -m $TASK_MODULE"]
