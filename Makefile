.PHONY: help install index dev-backend dev-frontend test eval eval-answer loadtest cov build docs up down clean

PY := .venv/Scripts/python.exe
UNAME := $(shell uname -s 2>/dev/null)

ifeq ($(OS),Windows_NT)
	PY := .venv/Scripts/python.exe
	ACTIVATE := .venv/Scripts/activate
else
	PY := .venv/bin/python
	ACTIVATE := .venv/bin/activate
endif

help:
	@echo "make install        安装后端 + 前端依赖"
	@echo "make index          重建知识库索引"
	@echo "make dev-backend    启动 FastAPI (127.0.0.1:8000)"
	@echo "make dev-frontend   启动 Vite (127.0.0.1:5173)"
	@echo "make test           运行 pytest 全量测试"
	@echo "make eval           跑检索评估（recall/MRR/误召率）"
	@echo "make build          构建前端产物"
	@echo "make docs           重新生成讲解文档 HTML"
	@echo "make up             docker compose 启动全栈"
	@echo "make down           停止容器"

install:
	python -m venv .venv || true
	$(PY) -m pip install -U pip
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm install --include=optional

index:
	cd backend && PYTHONPATH=. ../$(PY) -c "from app.rag.ingest import ingest_knowledge_base; print(ingest_knowledge_base().to_dict())"

dev-backend:
	cd backend && PYTHONPATH=. ../$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && PYTHONPATH=. ../$(PY) -m pytest -q

eval:
	cd backend && PYTHONPATH=. ../$(PY) scripts/eval_retrieval.py --k 5 --json eval/report.json

eval-answer:
	cd backend && PYTHONPATH=. ../$(PY) scripts/eval_answer.py --json eval/answer_report.json

loadtest:
	cd backend && PYTHONPATH=. ../$(PY) scripts/loadtest.py --port 8000 --concurrency 20 --requests 100

cov:
	cd backend && PYTHONPATH=. ../$(PY) scripts/coverage.py

build:
	cd frontend && npm run build

docs:
	cd frontend && node scripts/prerender-mermaid.mjs
	$(PY) scripts/build_docs.py docs/项目讲解.md
	$(PY) scripts/build_docs.py docs/面试拷打题库.md


up:
	docker compose up --build

down:
	docker compose down

clean:
	rm -rf backend/data backend/.pytest_cache frontend/dist
