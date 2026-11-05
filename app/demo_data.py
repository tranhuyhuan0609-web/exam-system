"""Demo content for local development (`python -m app.cli seed-demo`). Safe to run repeatedly."""

from sqlalchemy import func, select

from app.application.categories import create_category
from app.db import SessionLocal
from app.domain.question_hash import hash_of_choices
from app.models import Category, Difficulty, Exam, ExamMode, ExamQuestion, Question

R, U, A = Difficulty.RECOGNITION, Difficulty.UNDERSTANDING, Difficulty.APPLICATION

DEMO_SUBJECTS = [
    {
        "name": "Vật lí 11",
        "slug": "vat-li-11",
        "chapters": [
            (
                "Chương 1: Dao động",
                [
                    "Bài 1: Dao động điều hoà",
                    "Bài 2: Mô tả dao động điều hoà",
                    "Bài 3: Vận tốc, gia tốc trong dao động điều hoà",
                ],
            ),
            ("Chương 2: Sóng", ["Bài 8: Mô tả sóng", "Bài 9: Sóng ngang. Sóng dọc"]),
        ],
    },
    {
        "name": "Toán 11",
        "slug": "toan-11",
        "chapters": [
            (
                "Chương 1: Hàm số lượng giác và phương trình lượng giác",
                ["Bài 1: Giá trị lượng giác của góc lượng giác", "Bài 2: Công thức lượng giác"],
            ),
        ],
    },
]

# (lesson name prefix, difficulty, content, choices with the correct one first, explanation)
# Choices are stored in the order given; the correct one is the first entry and gets rotated below
# so the demo answers are not always "A".
DEMO_QUESTIONS = [
    (
        "Bài 1:",
        R,
        r"Một vật dao động điều hoà theo phương trình $x = A\cos(\omega t + \varphi)$. Đại lượng $A$ được gọi là",
        ["biên độ dao động", "tần số góc", "pha ban đầu", "li độ"],
        r"$A$ là biên độ: độ lệch cực đại của vật khỏi vị trí cân bằng.",
    ),
    (
        "Bài 1:",
        R,
        r"Trong phương trình dao động điều hoà $x = A\cos(\omega t + \varphi)$, đại lượng $(\omega t + \varphi)$ là",
        ["pha của dao động tại thời điểm $t$", "tần số dao động", "chu kì dao động", "biên độ dao động"],
        r"$(\omega t + \varphi)$ là pha dao động tại thời điểm $t$; $\varphi$ là pha ban đầu.",
    ),
    (
        "Bài 2:",
        U,
        r"Một vật dao động điều hoà với tần số góc $\omega = 4\pi$ rad/s. Chu kì dao động của vật là",
        ["$0{,}5$ s", "$2$ s", "$0{,}25$ s", "$4$ s"],
        r"$$T = \dfrac{2\pi}{\omega} = \dfrac{2\pi}{4\pi} = 0{,}5\ \text{s}$$",
    ),
    (
        "Bài 2:",
        U,
        r"Một vật dao động điều hoà với tần số $f = 5$ Hz. Tần số góc của dao động là",
        [r"$10\pi$ rad/s", r"$5\pi$ rad/s", r"$\dfrac{\pi}{5}$ rad/s", r"$2{,}5\pi$ rad/s"],
        r"$$\omega = 2\pi f = 2\pi \cdot 5 = 10\pi\ \text{rad/s}$$",
    ),
    (
        "Bài 3:",
        U,
        "Trong dao động điều hoà, tốc độ của vật đạt giá trị cực đại khi vật",
        ["đi qua vị trí cân bằng", "ở vị trí biên", "có li độ $x = \\dfrac{A}{2}$", "có gia tốc cực đại"],
        r"$|v| = \omega\sqrt{A^2 - x^2}$ lớn nhất khi $x = 0$, tức là khi vật qua vị trí cân bằng.",
    ),
    (
        "Bài 3:",
        A,
        r"Một vật dao động điều hoà với biên độ $A = 5$ cm và tần số góc $\omega = 10$ rad/s. Tốc độ cực đại của vật là",  # noqa: E501
        ["$50$ cm/s", "$2$ cm/s", "$0{,}5$ cm/s", "$500$ cm/s"],
        r"$$v_{\max} = \omega A = 10 \cdot 5 = 50\ \text{cm/s}$$",
    ),
    (
        "Bài 3:",
        A,
        r"Một vật dao động điều hoà có $A = 4$ cm, $\omega = 5$ rad/s. Độ lớn gia tốc cực đại của vật là",
        ["$100$ cm/s²", "$20$ cm/s²", "$80$ cm/s²", "$125$ cm/s²"],
        r"$$a_{\max} = \omega^2 A = 5^2 \cdot 4 = 100\ \text{cm/s}^2$$",
    ),
    (
        "Bài 3:",
        A,
        r"Một vật dao động điều hoà với $A = 10$ cm, $\omega = 2$ rad/s. Khi vật có li độ $x = 6$ cm thì tốc độ của vật là",  # noqa: E501
        ["$16$ cm/s", "$8$ cm/s", "$12$ cm/s", "$20$ cm/s"],
        r"$$v = \omega\sqrt{A^2 - x^2} = 2\sqrt{10^2 - 6^2} = 2 \cdot 8 = 16\ \text{cm/s}$$",
    ),
]

DEMO_EXAM_TITLE = "Luyện tập Chương 1: Dao động"


def _choices(options: list[str], rotate: int) -> list[dict]:
    choices = [{"id": f"c{i + 1}", "content": text, "is_correct": i == 0} for i, text in enumerate(options)]
    k = rotate % len(choices)
    return choices[k:] + choices[:k]


async def seed_demo() -> None:
    async with SessionLocal() as session:
        for data in DEMO_SUBJECTS:
            if await session.scalar(select(Category.id).where(Category.slug == data["slug"])):
                print(f"Skip subject {data['name']} (already exists).")
                continue
            subject = await create_category(session, data["name"], slug=data["slug"])
            for chapter_name, lessons in data["chapters"]:
                chapter = await create_category(session, chapter_name, subject)
                for lesson_name in lessons:
                    await create_category(session, lesson_name, chapter)
            print(f"Created subject {data['name']}.")
        await session.commit()

        physics = await session.scalar(select(Category).where(Category.slug == "vat-li-11"))
        if physics is None:
            return
        chapter1 = await session.scalar(
            select(Category).where(Category.parent_id == physics.id).order_by(Category.position).limit(1)
        )
        if chapter1 is None:
            return

        if await session.scalar(select(func.count()).select_from(Question).where(Question.root_id == physics.id)):
            print("Skip demo questions (subject already has questions).")
        else:
            # "Bài 1: Dao động điều hoà" -> "Bài 1:"
            children = (await session.scalars(select(Category).where(Category.parent_id == chapter1.id))).all()
            lessons = {lesson.name.split(":")[0] + ":": lesson for lesson in children}
            for i, (prefix, difficulty, content, options, explanation) in enumerate(DEMO_QUESTIONS):
                choices = _choices(options, rotate=i)
                session.add(
                    Question(
                        content_hash=hash_of_choices("MULTIPLE_CHOICE", content, choices),
                        root_id=physics.id,
                        category_id=lessons[prefix].id,
                        difficulty=difficulty,
                        content=content,
                        choices=choices,
                        explanation=explanation,
                    )
                )
            await session.commit()
            print(f"Created {len(DEMO_QUESTIONS)} demo questions.")

        if await session.scalar(select(Exam.id).where(Exam.title == DEMO_EXAM_TITLE)):
            print("Skip demo exam (already exists).")
            return
        branch = select(Category.id).where(Category.path.like(f"{chapter1.path}%"))
        question_ids = (
            await session.scalars(select(Question.id).where(Question.category_id.in_(branch)).order_by(Question.id))
        ).all()
        exam = Exam(
            root_id=physics.id,
            category_id=chapter1.id,
            title=DEMO_EXAM_TITLE,
            description="Đề luyện tập mẫu: 8 câu, 15 phút, làm lại không giới hạn.",
            exam_mode=ExamMode.PRACTICE,
            duration_seconds=15 * 60,
            is_published=bool(question_ids),
        )
        session.add(exam)
        await session.flush()
        session.add_all(
            ExamQuestion(exam_id=exam.id, question_id=qid, position=pos) for pos, qid in enumerate(question_ids, 1)
        )
        await session.commit()
        print(f"Created demo exam “{DEMO_EXAM_TITLE}” with {len(question_ids)} questions.")
