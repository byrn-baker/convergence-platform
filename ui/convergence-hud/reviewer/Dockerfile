FROM node:24-alpine
WORKDIR /app
COPY --chown=node:node reviewer /app/reviewer
COPY --chown=node:node modules/convergence /app/modules/convergence
USER node
EXPOSE 3444
CMD ["node", "reviewer/start.mjs"]
