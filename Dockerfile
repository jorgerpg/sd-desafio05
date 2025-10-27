FROM python:3.12-slim

WORKDIR /app
COPY server.py /app/server.py
COPY static /app/static
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV DB_PATH=/app/chat.db
EXPOSE 8000 8080

CMD ["/app/start.sh"]
