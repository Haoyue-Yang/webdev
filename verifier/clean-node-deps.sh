#!/bin/bash

# Script to recursively remove node_modules and package-lock.json files
# Usage: ./clean-node-deps.sh [directory]
# If no directory is specified, uses current directory

# Set the target directory (default to current directory)
TARGET_DIR="${1:-.}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting cleanup in: $TARGET_DIR${NC}"
echo ""

# Counter variables
node_modules_count=0
package_lock_count=0

# Find and remove node_modules directories
echo -e "${YELLOW}Removing node_modules directories...${NC}"
while IFS= read -r -d '' dir; do
    echo -e "${RED}Removing: $dir${NC}"
    rm -rf "$dir"
    ((node_modules_count++))
done < <(find "$TARGET_DIR" -name "node_modules" -type d -print0 2>/dev/null)

# Find and remove package-lock.json files
echo -e "${YELLOW}Removing package-lock.json files...${NC}"
while IFS= read -r -d '' file; do
    echo -e "${RED}Removing: $file${NC}"
    rm -f "$file"
    ((package_lock_count++))
done < <(find "$TARGET_DIR" -name "package-lock.json" -type f -print0 2>/dev/null)

# Summary
echo ""
echo -e "${GREEN}Cleanup complete!${NC}"
echo -e "${GREEN}Removed $node_modules_count node_modules directories${NC}"
echo -e "${GREEN}Removed $package_lock_count package-lock.json files${NC}"

# Optional: Show disk space freed (Linux/macOS)
if command -v du >/dev/null 2>&1; then
    echo ""z
    echo -e "${YELLOW}Current directory size: $(du -sh "$TARGET_DIR" 2>/dev/null | cut -f1)${NC}"
fi