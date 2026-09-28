import base64
import io
import json

from PIL import Image
from plan_image import IMAGE_SIZE, render, with_plan
from shop_generator import generate
from standardphysics_agents.training.prompt import room_view


def _view():
    graph, scenario = generate(2)[:2]
    return room_view(graph, scenario, [])


def test_the_plan_is_a_square_image_of_the_room():
    image = render(_view())
    assert image.size == (IMAGE_SIZE, IMAGE_SIZE)
    assert len(image.getcolors(maxcolors=IMAGE_SIZE * IMAGE_SIZE)) > 4


def test_a_feedback_message_gets_the_plan_of_its_room_and_keeps_its_json():
    message = {"role": "user", "content": json.dumps({"checker_feedback": {}, "room": _view()})}
    illustrated = with_plan(message)
    image_part, text_part = illustrated["content"]
    png = base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])
    assert Image.open(io.BytesIO(png)).size == (IMAGE_SIZE, IMAGE_SIZE)
    assert text_part["text"].endswith(message["content"])
