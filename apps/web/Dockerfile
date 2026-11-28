FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Rewrites are resolved at build time, so the API address is a build argument.
ARG VERITY_API_URL=http://api:8000
ENV VERITY_API_URL=$VERITY_API_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
# Middleware and the landing page call the API at run time (the /api rewrite is fixed at build time), so the
# runtime image needs the address too, not just the build stage.
ARG VERITY_API_URL=http://api:8000
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 VERITY_API_URL=$VERITY_API_URL
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
# The standalone output leaves public/ out: widget.js (the chatbot embed) and brand/ (icons, the email logo) live there.
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
