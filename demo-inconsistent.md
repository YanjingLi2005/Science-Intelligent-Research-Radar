# A Manuscript With Planted Inconsistencies

测试样例：故意埋了 2 处真矛盾 + 8 个"看起来像矛盾但其实没问题"的陷阱。
正确结果是只报那 2 处。答案见 `demo-inconsistent-ANSWERS.md`（跑完再看）。

## Abstract

Our system reaches 89.5 F1 on SQuAD. On the WMT 2014 English-to-German
translation task the model achieves 28.4 BLEU, improving over the best
previously reported results by over 2 BLEU. On the WMT 2014 English-to-French
task it establishes a state-of-the-art BLEU score of 41.8 after training for
3.5 days on eight GPUs. Averaged over the suite we see roughly 91 accuracy on
GLUE.

Earlier work [13] and follow-up studies [35, 2, 5] motivated this design.

## 3.2 Setup

We train on 8 GPUs for 12 epochs with 6 layers and 65 million parameters.

## 5.1 Results

On SQuAD the final model scores 84.2 F1.

Accuracy on GLUE is 91.2 across the nine tasks.

Accuracy on CIFAR is 93.1 on the first split and 87.4 accuracy on the second
split.

While single-head attention is 0.9 BLEU worse than the best setting, quality
also drops off with too many heads.

## 5.2 Model Variations

Our big model reaches 41.0 BLEU.

|model|BLEU|params|
|---|---|---|
|base|38.1|65|
|big|38.1|213|

## References

[1] Someone. A title. arXiv preprint arXiv:1601.07 F1, pages 2440-2448, 2016.
[2] Another. Accuracy of 99.9 in an unrelated venue, 2017.
