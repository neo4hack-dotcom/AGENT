# AGENT — development shortcuts.
.PHONY: help install dev api web build serve clean

PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip

help:
	@echo "make install   create the venv, install backend and frontend"
	@echo "make api       run the API on :3041 (hot reload)"
	@echo "make web       run the UI on :3040 (proxies /api to :3041)"
	@echo "make build     build the frontend into frontend/dist"
	@echo "make serve     production: one process serves the API and the UI"
	@echo "make clean     remove the venv, node_modules and the build"

install:
	python3 -m venv backend/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	cd frontend && npm install
	@test -f backend/.env || cp .env.example backend/.env
	@echo "Ready. 'make api' in one terminal, 'make web' in another."

api:
	cd backend && .venv/bin/uvicorn app.main:app --port 3041 --reload

web:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

serve: build
	cd backend && .venv/bin/uvicorn app.main:app --port 3041

clean:
	rm -rf backend/.venv frontend/node_modules frontend/dist backend/__pycache__
