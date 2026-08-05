# Painel Olimpo — desenvolvimento com hot reload.
# Para produção, trocar por build multi-stage com `next build` + output standalone.
FROM node:22-alpine

WORKDIR /app

RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
