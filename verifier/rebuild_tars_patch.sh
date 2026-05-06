cd ./UI-TARS-desktop/multimodal
pnpm -r run build 


echo "=== 删除已修改的 npm 包 ===" && \
rm -rf node_modules/.pnpm/@agent-tars+core@* && \
rm -rf node_modules/@agent-tars && \
echo "" && \
echo "=== 重新安装干净的 npm 包 ===" && \
pnpm install --prefer-offline 2>&1 | tail -20

node patch-dist.js