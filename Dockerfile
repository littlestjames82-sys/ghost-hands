# Ghost Hands MCP server, for directory checks (Glama) and local runs.
# Zero-dependency Python stdlib server over stdio. The default session
# body is the built-in fake/demo web, so the server starts and answers
# introspection (initialize, tools/list) with no browser installed.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

ENV GHOST_HANDS_DRIVER=fake

CMD ["ghost-hands", "mcp"]
