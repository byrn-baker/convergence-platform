FROM node:24-alpine
WORKDIR /app
COPY --chown=node:node . /app/reviewer
USER node
EXPOSE 3444
CMD ["node", "reviewer/start.mjs"]
