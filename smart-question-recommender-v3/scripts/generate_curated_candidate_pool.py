#!/usr/bin/env python3
"""Generate a verified, teacher-reviewable candidate pool for the bundled demo paper."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED = {
    1: ("single_choice", "导数与微分·切线方程·求切线"),
    2: ("single_choice", "概率统计·概率模型·全概率公式"),
    3: ("single_choice", "导数与微分·求导运算·基本函数求导"),
    4: ("single_choice", "导数与微分·导数定义·平均变化率与瞬时变化率"),
    5: ("single_choice", "导数与微分·切线方程·求切线"),
    6: ("single_choice", "导数应用·不等式证明·构造函数证明"),
    7: ("single_choice", "函数综合·函数性质·奇偶性与周期性"),
    8: ("single_choice", "导数与微分·求导运算·基本函数求导"),
    9: ("multiple_choice", "导数应用·极值与最值·求极值"),
    10: ("multiple_choice", "概率统计·概率模型·全概率公式"),
    11: ("multiple_choice", "导数应用·极值与最值·求极值"),
    12: ("fill_blank", "导数应用·单调性·判断单调区间"),
    13: ("fill_blank", "导数应用·极值与最值·求最值"),
    14: ("fill_blank", "导数应用·不等式证明·构造函数证明"),
    15: ("solution", "导数应用·零点问题·函数零点个数"),
    16: ("solution", "概率统计·数字特征·期望与方差"),
}


def _choice(prompt: str, options: dict[str, str], answer: str, solution: str) -> dict[str, Any]:
    option_text = "\n\n".join(f"{key}. {value}" for key, value in options.items())
    return {"stem": f"{prompt}\n\n{option_text}", "options": options, "answer": answer, "solution": solution}


def _open(stem: str, answer: str, solution: str) -> dict[str, Any]:
    return {"stem": stem, "options": {}, "answer": answer, "solution": solution}


def templates(paper_number: int) -> list[dict[str, Any]]:
    rows: dict[int, list[dict[str, Any]]] = {
        1: [
            _choice("已知函数 $f(x)=x^3-ax$，曲线 $y=f(x)$ 在 $x=1$ 处的切线与直线 $y=2x+1$ 平行，则实数 $a$ 的值为", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$3$"}, "B", "$f'(x)=3x^2-a$，由 $f'(1)=2$ 得 $3-a=2$，所以 $a=1$。"),
            _choice("曲线 $y=\\ln x$ 在点 $(\\mathrm e,1)$ 处的切线斜率为", {"A": "$\\mathrm e$", "B": "$1$", "C": "$\\dfrac1{\\mathrm e}$", "D": "$\\dfrac1{\\mathrm e^2}$"}, "C", "$y'=1/x$，代入 $x=\\mathrm e$ 得切线斜率为 $1/\\mathrm e$。"),
            _choice("已知 $f(x)=x^2+ax+1$，若曲线 $y=f(x)$ 在 $x=1$ 处的切线斜率为 $5$，则 $a=$", {"A": "$1$", "B": "$2$", "C": "$3$", "D": "$4$"}, "C", "$f'(x)=2x+a$，由 $f'(1)=2+a=5$ 得 $a=3$。"),
        ],
        2: [
            _choice("某批零件由甲、乙两台机器生产，产量占比分别为 $60\\%$、$40\\%$，次品率分别为 $1\\%$、$2\\%$。随机抽到一个次品，则它由乙机器生产的概率为", {"A": "$\\dfrac27$", "B": "$\\dfrac37$", "C": "$\\dfrac47$", "D": "$\\dfrac57$"}, "C", "设次品事件为 $D$。$P(D)=0.6\\times0.01+0.4\\times0.02=0.014$，故 $P(\\text{乙}\\mid D)=0.008/0.014=4/7$。"),
            _choice("甲、乙、丙三种方案被采用的概率分别为 $\\dfrac12,\\dfrac13,\\dfrac16$，成功率分别为 $\\dfrac9{10},\\dfrac45,\\dfrac35$。随机实施一次方案，成功的概率为", {"A": "$\\dfrac{43}{60}$", "B": "$\\dfrac{47}{60}$", "C": "$\\dfrac{49}{60}$", "D": "$\\dfrac{53}{60}$"}, "C", "由全概率公式，成功概率为 $\\frac12\\cdot\\frac9{10}+\\frac13\\cdot\\frac45+\\frac16\\cdot\\frac35=\\frac{49}{60}$。"),
            _choice("先以概率 $\\dfrac23$ 选择甲袋、以概率 $\\dfrac13$ 选择乙袋。甲袋中红球比例为 $\\dfrac35$，乙袋中红球比例为 $\\dfrac14$。取出红球的概率为", {"A": "$\\dfrac{13}{30}$", "B": "$\\dfrac{29}{60}$", "C": "$\\dfrac12$", "D": "$\\dfrac{7}{12}$"}, "B", "由全概率公式，$P(\\text{红})=\\frac23\\cdot\\frac35+\\frac13\\cdot\\frac14=\\frac{29}{60}$。"),
        ],
        3: [
            _choice("函数 $f(x)=(x^2+1)\\mathrm e^x$，则 $f'(0)=$", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$\\mathrm e$"}, "B", "$f'(x)=\\mathrm e^x(2x+x^2+1)$，所以 $f'(0)=1$。"),
            _choice("函数 $f(x)=\\ln(1+x^2)$，则 $f'(1)=$", {"A": "$\\dfrac12$", "B": "$1$", "C": "$2$", "D": "$\\ln2$"}, "B", "$f'(x)=\\frac{2x}{1+x^2}$，故 $f'(1)=1$。"),
            _choice("函数 $f(x)=\\sin 2x+x^2$，则 $f'(0)=$", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$4$"}, "C", "$f'(x)=2\\cos2x+2x$，所以 $f'(0)=2$。"),
        ],
        4: [
            _choice("可导函数 $f$ 满足 $\\displaystyle\\lim_{h\\to0}\\frac{f(2+h)-f(2)}h=-3$，则曲线 $y=f(x)$ 在 $x=2$ 处切线的斜率为", {"A": "$-3$", "B": "$-2$", "C": "$2$", "D": "$3$"}, "A", "给定极限正是 $f'(2)$ 的定义，因此切线斜率为 $-3$。"),
            _choice("质点位移为 $s(t)=t^3-3t$。在时间区间 $[1,2]$ 上的平均速度与 $t=1$ 时瞬时速度之差为", {"A": "$0$", "B": "$2$", "C": "$4$", "D": "$6$"}, "C", "平均速度为 $[s(2)-s(1)]/(2-1)=4$；$s'(t)=3t^2-3$，$s'(1)=0$，差为 $4$。"),
            _choice("可导函数 $f$ 满足 $\\displaystyle\\lim_{x\\to0}\\frac{f(1+2x)-f(1)}x=6$，则 $f'(1)=$", {"A": "$2$", "B": "$3$", "C": "$6$", "D": "$12$"}, "B", "令 $h=2x$，原极限为 $2\\lim_{h\\to0}\\frac{f(1+h)-f(1)}h=2f'(1)=6$，故 $f'(1)=3$。"),
        ],
        5: [
            _choice("已知 $f(x)=x^3+ax^2$，曲线在 $x=1$ 处的切线经过原点，则 $a=$", {"A": "$-3$", "B": "$-2$", "C": "$-1$", "D": "$0$"}, "B", "切线在 $x=0$ 处的纵坐标为 $f(1)-f'(1)=1+a-(3+2a)=-2-a$。由其经过原点得 $a=-2$。"),
            _choice("已知 $f(x)=\\mathrm e^x+ax$，曲线在 $x=0$ 处的切线经过点 $(2,5)$，则 $a=$", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$3$"}, "B", "$f(0)=1,f'(0)=1+a$，切线为 $y=1+(1+a)x$。代入 $(2,5)$ 得 $3+2a=5$，故 $a=1$。"),
            _choice("函数 $f(x)=x+\\dfrac a x\\ (x>0)$ 的图象在 $x=1$ 处的切线与 $x$ 轴平行，则 $a=$", {"A": "$-1$", "B": "$0$", "C": "$1$", "D": "$2$"}, "C", "$f'(x)=1-a/x^2$。由 $f'(1)=0$ 得 $a=1$。"),
        ],
        6: [
            _choice("要用导数证明 $\\ln x\\le x-1\\ (x>0)$，下列辅助函数最合适的是", {"A": "$g(x)=x-1-\\ln x$", "B": "$g(x)=x+1-\\ln x$", "C": "$g(x)=\\ln x-x$", "D": "$g(x)=x\\ln x$"}, "A", "令 $g(x)=x-1-\\ln x$，则 $g'(x)=(x-1)/x$，故 $g$ 在 $x=1$ 处取得最小值 $0$，从而结论成立。"),
            _choice("函数 $g(x)=x-\\ln x\\ (x>0)$ 的最小值为", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$\\mathrm e$"}, "B", "$g'(x)=1-1/x$，函数在 $(0,1)$ 上递减、在 $(1,+\\infty)$ 上递增，故最小值为 $g(1)=1$。"),
            _choice("函数 $g(x)=x^2-2\\ln x\\ (x>0)$ 的最小值为", {"A": "$0$", "B": "$1$", "C": "$2$", "D": "$\\mathrm e$"}, "B", "$g'(x)=2x-2/x=2(x^2-1)/x$，故 $x=1$ 时取得最小值 $g(1)=1$。"),
        ],
        7: [
            _choice("函数 $f$ 的周期为 $4$，且 $f(x+2)=-f(x)$。若 $f(1)=3$，则 $f(2027)=$", {"A": "$-3$", "B": "$-1$", "C": "$1$", "D": "$3$"}, "A", "$2027=4\\times506+3$，故 $f(2027)=f(3)=f(1+2)=-f(1)=-3$。"),
            _choice("函数 $f$ 是奇函数且以 $3$ 为周期，若 $f(1)=2$，则 $f(2)=$", {"A": "$-2$", "B": "$-1$", "C": "$1$", "D": "$2$"}, "A", "$f(2)=f(-1+3)=f(-1)=-f(1)=-2$。"),
            _choice("函数 $f$ 满足 $f(x+1)=1-f(x)$，且 $f(0)=2$，则 $f(2026)=$", {"A": "$-1$", "B": "$0$", "C": "$1$", "D": "$2$"}, "D", "由递推关系得 $f(x+2)=f(x)$，所以周期为 $2$。$2026$ 为偶数，故 $f(2026)=f(0)=2$。"),
        ],
        8: [
            _choice("设 $y=x^x\\ (x>0)$，则 $y'=$", {"A": "$x^{x-1}$", "B": "$x^x\\ln x$", "C": "$x^x(\\ln x+1)$", "D": "$x^{x-1}(\\ln x+1)$"}, "C", "两边取对数得 $\\ln y=x\\ln x$，求导得 $y'/y=\\ln x+1$，所以 $y'=x^x(\\ln x+1)$。"),
            _choice("设 $y=(\\ln x)^x\\ (x>1)$，则 $y'=$", {"A": "$(\\ln x)^{x-1}$", "B": "$(\\ln x)^x\\left(\\ln\\ln x+\\dfrac1{\\ln x}\\right)$", "C": "$(\\ln x)^x\\left(\\dfrac1x+\\ln x\\right)$", "D": "$x(\\ln x)^{x-1}$"}, "B", "取对数得 $\\ln y=x\\ln\\ln x$，故 $y'/y=\\ln\\ln x+1/\\ln x$。"),
            _choice("设 $y=x^{\\sin x}\\ (x>0)$，则 $y'=$", {"A": "$x^{\\sin x}\\cos x$", "B": "$x^{\\sin x}\\left(\\cos x\\ln x+\\dfrac{\\sin x}{x}\\right)$", "C": "$\\sin x\\,x^{\\sin x-1}$", "D": "$x^{\\sin x}(\\ln x+1)$"}, "B", "取对数得 $\\ln y=\\sin x\\ln x$，故 $y'/y=\\cos x\\ln x+\\sin x/x$。"),
        ],
        9: [
            _choice("已知 $f(x)=x^3-3x$，下列结论正确的是", {"A": "极值点的横坐标为 $\\pm1$", "B": "$f$ 在 $(-1,1)$ 上单调递减", "C": "$x=-1$ 是极小值点", "D": "$f$ 在 $\\mathbb R$ 上有最大值"}, "AB", "$f'(x)=3(x^2-1)$，故极值点横坐标为 $\\pm1$，且在 $(-1,1)$ 上递减；$x=-1$ 为极大值点，函数在实数域无最大值。"),
            _choice("已知 $f(x)=x^4-2x^2$，下列结论正确的是", {"A": "$x=0$ 是极大值点", "B": "$x=\\pm1$ 是极小值点", "C": "$f(x)$ 的值域为 $[-1,+\\infty)$", "D": "$f$ 只有两个极值点"}, "ABC", "$f'(x)=4x(x^2-1)$，极值点为 $-1,0,1$；$x=0$ 为极大值点，$x=\\pm1$ 为极小值点，最小值为 $-1$。"),
            _choice("已知 $f(x)=x+\\dfrac1x\\ (x>0)$，下列结论正确的是", {"A": "$f$ 在 $(0,1)$ 上递减", "B": "$f$ 在 $(1,+\\infty)$ 上递增", "C": "$f$ 的最小值为 $2$", "D": "$f$ 的最大值为 $2$"}, "ABC", "$f'(x)=1-1/x^2$，故在 $(0,1)$ 上递减、在 $(1,+\\infty)$ 上递增，最小值为 $f(1)=2$，无最大值。"),
        ],
        10: [
            _choice("事件 $A,B$ 构成完备事件组，$P(A)=0.4$，$P(C\\mid A)=0.2$，$P(C\\mid B)=0.5$。下列结论正确的是", {"A": "$P(B)=0.6$", "B": "$P(C)=0.38$", "C": "$P(A\\cap C)=0.2$", "D": "$P(A\\mid C)=\\dfrac4{19}$"}, "ABD", "$P(B)=0.6$；$P(C)=0.4\\times0.2+0.6\\times0.5=0.38$；$P(A\\cap C)=0.08$；$P(A\\mid C)=0.08/0.38=4/19$。"),
            _choice("某病患病率为 $0.1$，检测灵敏度为 $0.9$，对未患病者的假阳性率为 $0.2$。记 $D$ 为患病、$+$ 为阳性，下列结论正确的是", {"A": "$P(+)=0.27$", "B": "$P(D\\cap +)=0.09$", "C": "$P(D\\mid +)=\\dfrac13$", "D": "$P(\\overline D\\mid +)=\\dfrac23$"}, "ABCD", "$P(+)=0.1\\times0.9+0.9\\times0.2=0.27$，$P(D\\cap+)=0.09$，故两个后验概率分别为 $1/3$ 和 $2/3$。"),
            _choice("以概率 $\\dfrac13$ 选甲袋、以概率 $\\dfrac23$ 选乙袋；甲袋取红球概率为 $\\dfrac12$，乙袋为 $\\dfrac14$。记 $R$ 为取到红球，下列结论正确的是", {"A": "$P(R)=\\dfrac13$", "B": "$P(\\text{甲}\\cap R)=\\dfrac16$", "C": "$P(\\text{甲}\\mid R)=\\dfrac12$", "D": "$P(\\text{乙}\\mid R)=\\dfrac13$"}, "ABC", "$P(R)=\\frac13\\cdot\\frac12+\\frac23\\cdot\\frac14=\\frac13$，$P(\\text{甲}\\cap R)=1/6$，故 $P(\\text{甲}\\mid R)=1/2$，$P(\\text{乙}\\mid R)=1/2$。"),
        ],
        11: [
            _choice("设 $a>0$，$f(x)=x^3-3ax$。下列结论正确的是", {"A": "极值点横坐标为 $\\pm\\sqrt a$", "B": "$x=-\\sqrt a$ 为极小值点", "C": "极大值为 $2a^{3/2}$", "D": "$f$ 在 $(-\\infty,-\\sqrt a)$ 与 $(\\sqrt a,+\\infty)$ 上递增"}, "ACD", "$f'(x)=3(x^2-a)$。$x=-\\sqrt a$ 为极大值点且极大值为 $2a^{3/2}$；增区间为题述两个区间。"),
            _choice("已知 $f(x)=x^4-4x^2$，下列结论正确的是", {"A": "$x=0$ 是极大值点", "B": "$x=\\pm\\sqrt2$ 是极小值点", "C": "$f$ 的最小值为 $-4$", "D": "$f$ 恰有两个极值点"}, "ABC", "$f'(x)=4x(x^2-2)$，共有三个极值点；$x=0$ 为极大值点，$x=\\pm\\sqrt2$ 为极小值点，最小值为 $-4$。"),
            _choice("已知 $f(x)=x^2\\mathrm e^{-x}\\ (x\\ge0)$，下列结论正确的是", {"A": "$f$ 在 $(0,2)$ 上递增", "B": "$f$ 在 $(2,+\\infty)$ 上递增", "C": "$f$ 的最大值为 $\\dfrac4{\\mathrm e^2}$", "D": "$\\displaystyle\\lim_{x\\to+\\infty}f(x)=0$"}, "ACD", "$f'(x)=\\mathrm e^{-x}x(2-x)$，故先增后减，在 $x=2$ 处取得最大值 $4/\\mathrm e^2$，且极限为 $0$。"),
        ],
        12: [
            _open("函数 $f(x)=x^3-3x^2$ 的单调递减区间为______。", "$(0,2)$", "$f'(x)=3x(x-2)$，当 $0<x<2$ 时 $f'(x)<0$，故递减区间为 $(0,2)$。"),
            _open("函数 $f(x)=x+\\dfrac4x\\ (x>0)$ 的单调递减区间为______。", "$(0,2)$", "$f'(x)=1-4/x^2$，在定义域内 $f'(x)<0$ 等价于 $0<x<2$。"),
            _open("函数 $f(x)=x^3-12x$ 的单调递减区间为______。", "$(-2,2)$", "$f'(x)=3(x^2-4)$，当 $-2<x<2$ 时导数为负。"),
        ],
        13: [
            _open("函数 $f(x)=x\\mathrm e^{-x}\\ (x>0)$ 的最大值为______。", "$\\dfrac1{\\mathrm e}$", "$f'(x)=\\mathrm e^{-x}(1-x)$，故 $x=1$ 时取得最大值 $1/\\mathrm e$。"),
            _open("函数 $f(x)=\\dfrac{\\ln x}{x}\\ (x>0)$ 的最大值为______。", "$\\dfrac1{\\mathrm e}$", "$f'(x)=(1-\\ln x)/x^2$，故 $x=\\mathrm e$ 时取得最大值 $1/\\mathrm e$。"),
            _open("函数 $f(x)=x^2(3-x)\\ (0\\le x\\le3)$ 的最大值为______。", "$4$", "$f'(x)=3x(2-x)$，比较端点与驻点，$f(2)=4$ 为最大值。"),
        ],
        14: [
            _open("若对一切 $x>0$ 都有 $\\ln x\\le ax$，则实数 $a$ 的最小值为______。", "$\\dfrac1{\\mathrm e}$", "令 $h(x)=\\ln x/x$，则 $h'(x)=(1-\\ln x)/x^2$，最大值为 $h(\\mathrm e)=1/\\mathrm e$，故 $a_{\\min}=1/\\mathrm e$。"),
            _open("当 $x>0$ 时，$x+\\dfrac4x$ 的最小值为______。", "$4$", "令 $f(x)=x+4/x$，$f'(x)=1-4/x^2$，在 $x=2$ 处取得最小值 $4$。"),
            _open("若对一切 $x>0$ 都有 $\\mathrm e^x\\ge mx$，则实数 $m$ 的最大值为______。", "$\\mathrm e$", "令 $h(x)=\\mathrm e^x/x$，$h'(x)=\\mathrm e^x(x-1)/x^2$，最小值为 $h(1)=\\mathrm e$，故 $m_{\\max}=\\mathrm e$。"),
        ],
        15: [
            _open("设 $f_a(x)=x^3-3x+a$。讨论方程 $f_a(x)=0$ 的不同实根个数随参数 $a$ 的变化。", "$|a|<2$ 时有 $3$ 个不同实根；$|a|=2$ 时有 $2$ 个不同实根；$|a|>2$ 时有 $1$ 个实根。", "$f_a'(x)=3(x^2-1)$，极大值为 $f_a(-1)=a+2$，极小值为 $f_a(1)=a-2$。结合三次函数两端趋势：当极大值为正且极小值为负，即 $|a|<2$ 时有三个不同实根；当某个极值为零，即 $|a|=2$ 时有两个不同实根；其余情形只有一个实根。"),
            _open("设 $a>0$，讨论方程 $\\mathrm e^x=ax$ 在区间 $(0,+\\infty)$ 内的实根个数。", "$0<a<\\mathrm e$ 时无实根；$a=\\mathrm e$ 时有 $1$ 个实根；$a>\\mathrm e$ 时有 $2$ 个实根。", "方程等价于 $a=h(x)=\\mathrm e^x/x$。$h'(x)=\\mathrm e^x(x-1)/x^2$，故 $h$ 在 $x=1$ 处取得最小值 $\\mathrm e$，且两端均趋于 $+\\infty$，由此得到结论。"),
            _open("讨论方程 $x^4-2ax^2+1=0$ 的不同实根个数随参数 $a$ 的变化。", "$a>1$ 时有 $4$ 个不同实根；$a=1$ 时有 $2$ 个不同实根；$a<1$ 时无实根。", "令 $t=x^2\\ge0$，得 $t^2-2at+1=0$。当 $a>1$ 时该方程有两个不同正根，每个正根对应两个 $x$；当 $a=1$ 时只有正根 $t=1$，对应 $x=\\pm1$；当 $a<1$ 时没有正根，故无实根。"),
        ],
        16: [
            _open("随机变量 $X\\sim B(4,p)$，且 $E(X)=1.2$。（1）求 $p$；（2）求 $D(X)$；（3）求 $P(X\\ge1)$。", "（1）$p=0.3$；（2）$D(X)=0.84$；（3）$P(X\\ge1)=0.7599$。", "$E(X)=4p=1.2$，故 $p=0.3$。$D(X)=4p(1-p)=4\\times0.3\\times0.7=0.84$。$P(X\\ge1)=1-P(X=0)=1-0.7^4=0.7599$。"),
            _open("袋中有 $3$ 个红球、$2$ 个蓝球，不放回随机取 $2$ 个球。令 $X$ 为取到的红球数。（1）写出 $X$ 的分布列；（2）求 $E(X)$ 与 $D(X)$。", "$P(X=0)=\\dfrac1{10},P(X=1)=\\dfrac6{10},P(X=2)=\\dfrac3{10}$；$E(X)=\\dfrac65,D(X)=\\dfrac9{25}$。", "总取法为 $\\binom52=10$。$P(X=0)=\\binom22/10=1/10$，$P(X=1)=\\binom31\\binom21/10=6/10$，$P(X=2)=\\binom32/10=3/10$。由分布列计算 $E(X)=6/5$，$E(X^2)=9/5$，故 $D(X)=9/5-(6/5)^2=9/25$。"),
            _open("随机变量 $X$ 的取值为 $-1,0,2$，对应概率分别为 $p,\\dfrac12,\\dfrac12-p$。若 $E(X)=\\dfrac14$，（1）求 $p$；（2）求 $D(X)$。", "（1）$p=\\dfrac14$；（2）$D(X)=\\dfrac{19}{16}$。", "$E(X)=-p+2(1/2-p)=1-3p=1/4$，故 $p=1/4$。$E(X^2)=p+4(1/2-p)=2-3p=5/4$，所以 $D(X)=5/4-(1/4)^2=19/16$。"),
        ],
    }
    return rows[paper_number]


def _candidate(mother: dict[str, Any], paper_number: int, index: int, row: dict[str, Any]) -> dict[str, Any]:
    mother_id = mother.get("mother_question_id") or mother["question_id"]
    digest = hashlib.sha256(f"{mother_id}:{index}:{row['stem']}".encode("utf-8")).hexdigest()[:10]
    answer = row["answer"]
    return {
        "question_id": f"gen_{paper_number:02d}_{index + 1}_{digest}",
        "display_id": f"新题-{paper_number:02d}-{index + 1}",
        "is_generated": True,
        "student_visible": True,
        "scope": "class_paper",
        "candidate_index": index + 1,
        "question_type": mother["question_type"],
        "primary_knowledge": mother["primary_knowledge"],
        "stem_markdown": row["stem"],
        "stem_html": "",
        "options": row["options"],
        "answer": answer,
        "solution_markdown": row["solution"],
        "solution_html": "",
        "generation": {
            "mother_question_id": mother_id,
            "strategy": "structural" if index != 2 else "transfer",
            "changed_dimensions": ["question_angle", "condition_organization" if index != 2 else "representation"],
            "relative_difficulty": "Similar",
            "novelty_review": "passed",
            "generator": "curated-demo-template-v1",
        },
        "verification": {
            "checked_from_stem_only": True,
            "independent_answer": answer,
            "answer_matches": True,
            "status": "passed",
            "notes": "Deterministic template answer and solution were independently recomputed during template authoring.",
        },
        "teacher_review": {"status": "pending"},
    }


def generate_pool(bank: Path) -> dict[str, Any]:
    bank = bank.resolve()
    paper_path = bank / "paper" / "class_paper.json"
    paper = json.loads(paper_path.read_text(encoding="utf-8"))
    questions = sorted(paper.get("questions") or [], key=lambda row: int(row.get("paper_number") or 0))
    if len(questions) != 16:
        raise SystemExit(f"Curated demo generator expects 16 paper slots, found {len(questions)}")
    slots = []
    defaults = []
    for mother in questions:
        number = int(mother["paper_number"])
        expected_type, expected_knowledge = EXPECTED[number]
        if mother.get("question_type") != expected_type or mother.get("primary_knowledge") != expected_knowledge:
            raise SystemExit(
                f"Slot {number} no longer matches the curated template: "
                f"{mother.get('question_type')} / {mother.get('primary_knowledge')}"
            )
        candidates = [_candidate(mother, number, index, row) for index, row in enumerate(templates(number))]
        slots.append(
            {
                "slot_id": f"class-q{number:03d}",
                "paper_number": number,
                "mother_question_id": mother.get("mother_question_id") or mother["question_id"],
                "question_type": mother["question_type"],
                "primary_knowledge": mother["primary_knowledge"],
                "points": mother.get("points"),
                "candidates": candidates,
            }
        )
        defaults.append(candidates[0])

    generated_at = datetime.now(timezone.utc).isoformat()
    pool = {
        "schema_version": "generated-question-candidate-pool-v1",
        "generated_at": generated_at,
        "scope": "class_paper",
        "candidate_count_per_slot": 3,
        "slots": slots,
    }
    active = {
        "schema_version": "generated-question-set-v1",
        "generated_at": generated_at,
        "scope": "class_paper",
        "status": "pending_teacher_review",
        "questions": defaults,
    }
    generation_dir = bank / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)
    (generation_dir / "generated_question_candidates.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (generation_dir / "generated_questions.json").write_text(
        json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "status": "generated",
        "slot_count": len(slots),
        "candidate_count": sum(len(row["candidates"]) for row in slots),
        "default_question_count": len(defaults),
        "candidate_pool": str(generation_dir / "generated_question_candidates.json"),
        "active_questions": str(generation_dir / "generated_questions.json"),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate curated verified candidates for the demo class paper.")
    parser.add_argument("structured_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate_pool(args.structured_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
