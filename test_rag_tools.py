#!/usr/bin/env python3
"""Test the new RAG introspection tools."""
import sys
sys.path.insert(0, '/home/cwatt250/AI_Agent')

from tools.rag_tool import memory_stats, memory_list

print("Testing memory_stats...")
result = memory_stats.invoke({})
print(result)
print()

print("Testing memory_list...")
result = memory_list.invoke({"limit": 5})
print(result)
print()

print("All RAG introspection tools working!")
