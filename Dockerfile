FROM python:3.12-slim
WORKDIR /app
COPY observer.py /app/observer.py
COPY market_observer /app/market_observer
RUN useradd --uid 10001 --create-home observer && mkdir /data && chown observer:observer /data
USER observer
CMD ["python", "-u", "observer.py", "--base", "TAO", "--loop", "--db", "/data/trading_decision_lab.sqlite"]
