# Phase 13 — Speed Layer verification

Ollama at http://localhost:11434, router model = qwen3:4b.

## Cold pass (model evicted before each call — pre-Phase-13 baseline)

  cold  0  ttf=  530.2ms  'hi'
  cold  1  ttf=  528.0ms  'what is 2+2?'
  cold  2  ttf=  527.5ms  'yes or no?'
  cold  3  ttf=  520.5ms  'ack'
  cold  4  ttf=  525.8ms  'thanks'
  cold  5  ttf=  534.7ms  'in 2 sentences, what is a hash table?'
  cold  6  ttf=  526.1ms  'list 3 reasons to use postgres over sqlite'
  cold  7  ttf=  546.1ms  'what does git rebase do?'
  cold  8  ttf=  548.4ms  'explain a kalman filter briefly'
  cold  9  ttf=  527.5ms  'mutex vs semaphore?'

## Warm pass (router pinned via prewarm + KEEP_ALIVE=-1)

  warm  0  ttf=   78.8ms  'hi'
  warm  1  ttf=   77.8ms  'what is 2+2?'
  warm  2  ttf=  103.8ms  'yes or no?'
  warm  3  ttf=   81.3ms  'ack'
  warm  4  ttf=   78.7ms  'thanks'
  warm  5  ttf=  136.3ms  'in 2 sentences, what is a hash table?'
  warm  6  ttf=   75.5ms  'list 3 reasons to use postgres over sqlite'
  warm  7  ttf=   88.0ms  'what does git rebase do?'
  warm  8  ttf=  116.7ms  'explain a kalman filter briefly'
  warm  9  ttf=   76.9ms  'mutex vs semaphore?'

| pass | n  | mean ttf | median ttf |
|------|----|----------|------------|
| cold | 10 |   531.5ms |   527.7ms |
| warm | 10 |    91.4ms |    80.1ms |

**Mean TTF speedup**:    82.8%  → PASS
**Median TTF speedup**:  84.8%  → PASS

**Phase 13 exit criterion (>=50% TTF reduction)**: PASS
