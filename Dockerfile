# tolstack web app (Streamlit).
#   docker build -t tolstack .
#   docker run --rm -p 8501:8501 tolstack      ->  http://localhost:8501
FROM python:3.12-slim

# OpenCascade (cadquery-ocp) links against the GL/X11 libraries even though nothing is drawn on screen.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Run as a normal user: uploads and reports only go to the temp folder (one folder per browser session).
RUN groupadd -g 1000 appuser && useradd -m -u 1000 -g appuser appuser

WORKDIR /app
COPY pyproject.toml README.md ./
COPY tolstack ./tolstack
RUN pip install --no-cache-dir ".[app]"
COPY app.py ./
COPY .streamlit ./.streamlit

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

USER appuser
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
