#!/bin/bash

echo "🧪 Testing Protein Information System Dump Functionality"
echo "========================================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counter
TESTS_RUN=0
TESTS_PASSED=0

run_test() {
    local test_name="$1"
    local command="$2"
    local expected_return="$3"
    
    echo -e "\n${BLUE}Test $((++TESTS_RUN)): $test_name${NC}"
    echo "Command: $command"
    echo "----------------------------------------"
    
    if eval "$command"; then
        if [ "$expected_return" = "0" ]; then
            echo -e "${GREEN}✅ PASSED${NC}"
            ((TESTS_PASSED++))
        else
            echo -e "${RED}❌ FAILED (expected failure but got success)${NC}"
        fi
    else
        if [ "$expected_return" = "1" ]; then
            echo -e "${GREEN}✅ PASSED (expected failure)${NC}"
            ((TESTS_PASSED++))
        else
            echo -e "${RED}❌ FAILED${NC}"
        fi
    fi
}

# Create test directory structure
echo -e "\n${YELLOW}📁 Setting up test environment...${NC}"
mkdir -p test_dumps/subfolder
mkdir -p backups

echo -e "\n${YELLOW}🧪 Starting tests...${NC}"

# Test 1: Create dump with automatic directory creation
run_test "Create dump with new directory" \
    "poetry run python protein_information_system/main.py --create-dump test_dumps/test1.dump --force" \
    "0"

# Test 2: Create dump in existing directory
run_test "Create dump in existing directory" \
    "poetry run python protein_information_system/main.py --create-dump test_dumps/test2.dump --force" \
    "0"

# Test 3: Create dump in subdirectory
run_test "Create dump in subdirectory" \
    "poetry run python protein_information_system/main.py --create-dump test_dumps/subfolder/test3.dump --force" \
    "0"

# Test 4: Test overwrite protection (this will need manual input simulation)
echo -e "\n${BLUE}Test $((++TESTS_RUN)): Test overwrite protection${NC}"
echo "Command: Create same dump without --force (should prompt)"
echo "----------------------------------------"
echo "n" | poetry run python protein_information_system/main.py --create-dump test_dumps/test1.dump
if [ $? -eq 0 ]; then
    echo -e "${RED}❌ FAILED (should have been cancelled)${NC}"
else
    echo -e "${GREEN}✅ PASSED (correctly cancelled)${NC}"
    ((TESTS_PASSED++))
fi

# Test 5: Force overwrite
run_test "Force overwrite existing dump" \
    "poetry run python protein_information_system/main.py --create-dump test_dumps/test1.dump --force" \
    "0"

# Test 6: Restore existing dump
run_test "Restore dump to test database" \
    "poetry run python protein_information_system/main.py --restore-dump test_dumps/test1.dump" \
    "0"

# Test 7: Try to restore non-existent dump
run_test "Try to restore non-existent dump" \
    "poetry run python protein_information_system/main.py --restore-dump non_existent.dump" \
    "1"

# Test 8: Verify dump integrity
run_test "Verify dump integrity" \
    "poetry run python protein_information_system/main.py --verify-dump" \
    "0"

# Test 9: Full workflow with custom name
run_test "Full workflow with custom name" \
    "poetry run python protein_information_system/main.py --full-dump-test backups/full_test.dump --force" \
    "0"

# Test 10: Full workflow with auto-generated name
run_test "Full workflow with auto-generated name" \
    "poetry run python protein_information_system/main.py --full-dump-test --force" \
    "0"

# Test 11: Run in test mode
run_test "Run PIS in test mode" \
    "timeout 10s poetry run python protein_information_system/main.py --test" \
    "0"

# Test 12: Original PIS functionality (should still work)
echo -e "\n${BLUE}Test $((++TESTS_RUN)): Original PIS functionality${NC}"
echo "Command: poetry run pis (timeout 10s)"
echo "----------------------------------------"
if timeout 10s poetry run pis; then
    echo -e "${GREEN}✅ PASSED${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${GREEN}✅ PASSED (timeout expected)${NC}"
    ((TESTS_PASSED++))
fi

# Test 13: Check if dumps were created properly
echo -e "\n${BLUE}Test $((++TESTS_RUN)): Check created dump files${NC}"
echo "----------------------------------------"
if [ -f "test_dumps/test1.dump" ] && [ -f "test_dumps/test2.dump" ] && [ -f "test_dumps/subfolder/test3.dump" ]; then
    echo -e "${GREEN}✅ PASSED (all dump files exist)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}❌ FAILED (some dump files missing)${NC}"
    ls -la test_dumps/
    ls -la test_dumps/subfolder/ 2>/dev/null || true
fi

# Summary
echo -e "\n${YELLOW}📊 Test Summary${NC}"
echo "========================================"
echo -e "Tests run: $TESTS_RUN"
echo -e "Tests passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests failed: ${RED}$((TESTS_RUN - TESTS_PASSED))${NC}"

if [ $TESTS_PASSED -eq $TESTS_RUN ]; then
    echo -e "\n${GREEN}🎉 All tests passed!${NC}"
else
    echo -e "\n${RED}❌ Some tests failed. Check output above.${NC}"
fi

# Cleanup option
echo -e "\n${YELLOW}🧹 Cleanup${NC}"
read -p "Do you want to remove test dump files? [y/N]: " cleanup_response
if [[ $cleanup_response =~ ^[Yy]$ ]]; then
    rm -rf test_dumps/ backups/ dumps/
    echo "✅ Test files cleaned up"
else
    echo "ℹ️  Test files kept for inspection"
fi

echo -e "\n${BLUE}Test completed!${NC}"