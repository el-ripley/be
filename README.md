docker exec -it ai-agent-postgres-1 psql -U el-ripley-user -d el_ripley
docker exec ai-agent-postgres-1 pg_dump -U el-ripley-user -d el_ripley > backup.sql

docker exec -it be-ai-agent-postgres-1 psql -U el-ripley-user -d el_ripley


docker compose -f docker-compose.infra.yml down -v && docker compose -f docker-compose.infra.yml up -d && sleep 3 && ./scripts/init_postgres.sh && poetry run python src/main.py
