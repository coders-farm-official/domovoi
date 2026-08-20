FROM postgres:16-alpine

ENV POSTGRES_DB=ltldb
ENV POSTGRES_USER=ltluser
ENV POSTGRES_PASSWORD=ltlpass

# Only the table definitions run at init. init.sql's leading CREATE
# DATABASE / CREATE USER lines are for a hand-run bootstrap against an
# existing server; the image's entrypoint has already created both from
# the environment above, so those statements are expected to no-op with
# a "already exists" notice.
COPY init.sql /docker-entrypoint-initdb.d/

EXPOSE 5432
