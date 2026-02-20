# how2.md

Short cheat sheet for terminal commands and GitHub-only deploy.

## 1) Git base
```bash
git status
git pull
git checkout -b feature/my-change
git add .
git commit -m "feat: short message"
git push -u origin feature/my-change
```

## 2) Ship to main (via PR)
```bash
git checkout main
git pull
git merge --no-ff feature/my-change
git push origin main
```

## 3) Minimal local check
```bash
python server/manage.py check
```

## 4) Deploy rule
- No manual `scp`.
- Deploy only by GitHub Actions on push to `main`.
- Workflow file: `.github/workflows/deploy.yml`.

## 5) Required GitHub secret (only one)
GitHub -> Settings -> Secrets and variables -> Actions:
- `DEPLOY_PASSWORD` = your server SSH password for user `root`

Fixed in workflow:
- host: `93.170.72.31`
- port: `22`
- user: `root`
- app dir: `/opt/synkro/app`

## 6) How to deploy
1. Push branch to GitHub.
2. Open PR to `main`.
3. Merge PR.
4. GitHub Action deploys automatically.

## 7) Quick server checks
```bash
docker compose ps
docker compose logs --tail=100 web
docker compose logs --tail=100 worker
docker compose logs --tail=100 beat
```
