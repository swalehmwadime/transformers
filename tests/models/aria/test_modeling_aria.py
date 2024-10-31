# coding=utf-8
# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Testing suite for the PyTorch Aria model."""

import gc
import unittest

import requests

from transformers import (
    AriaConfig,
    AriaForConditionalGeneration,
    AriaTextConfig,
    AutoProcessor,
    AutoTokenizer,
    is_torch_available,
    is_vision_available,
)
from transformers.models.idefics3 import Idefics3VisionConfig
from transformers.testing_utils import (
    require_bitsandbytes,
    require_torch,
    require_torch_gpu,
    require_vision,
    slow,
    torch_device,
)

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, floats_tensor, ids_tensor


if is_torch_available():
    import torch
else:
    is_torch_greater_or_equal_than_2_0 = False

if is_vision_available():
    from PIL import Image


class AriaVisionText2TextModelTester:
    def __init__(
        self,
        parent,
        ignore_index=-100,
        image_token_index=0,
        projector_hidden_act="gelu",
        seq_length=7,
        vision_feature_select_strategy="default",
        vision_feature_layer=-1,
        text_config=AriaTextConfig(
            seq_length=7,
            is_training=True,
            use_input_mask=True,
            use_token_type_ids=False,
            use_labels=True,
            hidden_act="gelu",
            hidden_dropout_prob=0.1,
            attention_probs_dropout_prob=0.1,
            type_vocab_size=16,
            type_sequence_label_size=2,
            initializer_range=0.02,
            num_labels=3,
            num_choices=4,
            pad_token_id=1,
            hidden_size=32,
            intermediate_size=64,
            max_position_embeddings=60,
            model_type="aria_moe_lm",
            moe_intermediate_size=4,
            moe_num_experts=4,
            moe_topk=2,
            num_attention_heads=20,
            num_experts_per_tok=3,
            num_hidden_layers=2,
            num_key_value_heads=20,
            rope_theta=5000000,
            vocab_size=99,
        ),
        is_training=True,
        vision_config=Idefics3VisionConfig(
            image_size=358,
            patch_size=10,
            num_channels=3,
            is_training=True,
            hidden_size=32,
            projection_dim=20,
            num_hidden_layers=2,
            num_attention_heads=16,
            intermediate_size=10,
            dropout=0.1,
            attention_dropout=0.1,
            initializer_range=0.02,
        ),
    ):
        self.parent = parent
        self.ignore_index = ignore_index
        self.image_token_index = image_token_index
        self.projector_hidden_act = projector_hidden_act
        self.vision_feature_select_strategy = vision_feature_select_strategy
        self.vision_feature_layer = vision_feature_layer
        self.text_config = text_config
        self.vision_config = vision_config
        self.pad_token_id = text_config.pad_token_id

        self.num_hidden_layers = text_config.num_hidden_layers
        self.vocab_size = text_config.vocab_size
        self.hidden_size = text_config.hidden_size
        self.num_attention_heads = text_config.num_attention_heads
        self.is_training = is_training

        self.batch_size = 10
        self.num_channels = 3
        self.image_size = 358
        self.num_image_tokens = 128  # fix pour attention size
        self.seq_length = seq_length + self.num_image_tokens

    def get_config(self):
        return AriaConfig(
            text_config=self.text_config,
            vision_config=self.vision_config,
            ignore_index=self.ignore_index,
            image_token_index=self.image_token_index,
            projector_hidden_act=self.projector_hidden_act,
            vision_feature_select_strategy=self.vision_feature_select_strategy,
            vision_feature_layer=self.vision_feature_layer,
        )

    def prepare_config_and_inputs(self):
        pixel_values = floats_tensor(
            [
                self.batch_size,
                self.vision_config.num_channels,
                self.vision_config.image_size,
                self.vision_config.image_size,
            ]
        )
        config = self.get_config()

        return config, pixel_values

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, pixel_values = config_and_inputs
        input_ids = ids_tensor([self.batch_size, self.seq_length], config.text_config.vocab_size - 1) + 1
        attention_mask = input_ids.ne(1).to(torch_device)
        input_ids[input_ids == config.image_token_index] = self.pad_token_id
        input_ids[:, : self.num_image_tokens] = config.image_token_index
        inputs_dict = {
            "pixel_values": pixel_values,
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        return config, inputs_dict

    def create_and_check_aria_model_fp16_forward(self, config, input_ids, pixel_values, attention_mask):
        model = AriaForConditionalGeneration(config=config)
        model.to(torch_device)
        model.eval()
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            logits = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                pixel_values=pixel_values.to(torch.bfloat16),
                return_dict=True,
            )["logits"]
        self.parent.assertFalse(torch.isnan(logits).any().item())


@require_torch
class AriaForConditionalGenerationModelTest(ModelTesterMixin, GenerationTesterMixin, unittest.TestCase):
    """
    Model tester for `AriaForConditionalGeneration`.
    """

    all_model_classes = (AriaForConditionalGeneration,) if is_torch_available() else ()
    all_generative_model_classes = (AriaForConditionalGeneration,) if is_torch_available() else ()
    test_pruning = False
    test_head_masking = False

    def setUp(self):
        self.model_tester = AriaVisionText2TextModelTester(self)
        self.config_tester = ConfigTester(self, config_class=AriaConfig, has_text_modality=False)

    # overwrite inputs_embeds tests because we need to delete "pixel values" for LVLMs
    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.to(torch_device)
            model.eval()

            inputs = self._prepare_for_class(inputs_dict, model_class)

            input_ids = inputs["input_ids"]
            del inputs["input_ids"]
            del inputs["pixel_values"]

            wte = model.get_input_embeddings()
            inputs["inputs_embeds"] = wte(input_ids)

            with torch.no_grad():
                model(**inputs)

    # overwrite inputs_embeds tests because we need to delete "pixel values" for LVLMs
    # while some other models require pixel_values to be present
    def test_inputs_embeds_matches_input_ids(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.to(torch_device)
            model.eval()

            inputs = self._prepare_for_class(inputs_dict, model_class)
            input_ids = inputs["input_ids"]
            del inputs["input_ids"]
            del inputs["pixel_values"]

            inputs_embeds = model.get_input_embeddings()(input_ids)

            with torch.no_grad():
                out_ids = model(input_ids=input_ids, **inputs)[0]
                out_embeds = model(inputs_embeds=inputs_embeds, **inputs)[0]
            self.assertTrue(torch.allclose(out_embeds, out_ids))

    @unittest.skip(
        reason="This architecure seem to not compute gradients properly when using GC, check: https://github.com/huggingface/transformers/pull/27124"
    )
    def test_training_gradient_checkpointing(self):
        pass

    @unittest.skip(
        reason="This architecure seem to not compute gradients properly when using GC, check: https://github.com/huggingface/transformers/pull/27124"
    )
    def test_training_gradient_checkpointing_use_reentrant(self):
        pass

    @unittest.skip(
        reason="This architecure seem to not compute gradients properly when using GC, check: https://github.com/huggingface/transformers/pull/27124"
    )
    def test_training_gradient_checkpointing_use_reentrant_false(self):
        pass

    @unittest.skip(reason="Compile not yet supported because in LLava models")
    def test_sdpa_can_compile_dynamic(self):
        pass

    @unittest.skip(reason="Compile not yet supported because in LLava models")
    def test_sdpa_can_dispatch_on_flash(self):
        pass

    @unittest.skip(reason="")
    def test_new_cache_format_0(self):
        pass

    @unittest.skip(reason="")
    def test_new_cache_format_1(self):
        pass

    @unittest.skip(reason="")
    def test_new_cache_format_2(self):
        pass

    @unittest.skip(reason="Feedforward chunking is not yet supported")
    def test_feed_forward_chunking(self):
        pass

    @unittest.skip(reason="Unstable test")
    def test_initialization(self):
        pass

    @unittest.skip(reason="Unstable test")
    def test_dola_decoding_sample(self):
        pass


@require_torch
class AriaForConditionalGenerationIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.processor = AutoProcessor.from_pretrained("rhymes-ai/Aria")

    def tearDown(self):
        gc.collect()
        torch.cuda.empty_cache()

    @slow
    @require_bitsandbytes
    def test_small_model_integration_test(self):
        # Let' s make sure we test the preprocessing to replace what is used
        model = AriaForConditionalGeneration.from_pretrained("rhymes-ai/Aria", load_in_4bit=True)

        prompt = "<image>\nUSER: What are the things I should be cautious about when I visit this place?\nASSISTANT:"
        image_file = "https://aria-vl.github.io/static/images/view.jpg"
        raw_image = Image.open(requests.get(image_file, stream=True).raw)
        inputs = self.processor(images=raw_image, text=prompt, return_tensors="pt")

        EXPECTED_INPUT_IDS = torch.tensor([[1, 32000, 28705, 13, 11123, 28747, 1824, 460, 272, 1722,315, 1023, 347, 13831, 925, 684, 739, 315, 3251, 456,1633, 28804, 13, 4816, 8048, 12738, 28747]])  # fmt: skip
        self.assertTrue(torch.equal(inputs["input_ids"], EXPECTED_INPUT_IDS))

        output = model.generate(**inputs, max_new_tokens=20)
        EXPECTED_DECODED_TEXT = "\nUSER: What are the things I should be cautious about when I visit this place?\nASSISTANT: When visiting this place, there are a few things one should be cautious about. Firstly,"  # fmt: skip

        self.assertEqual(
            self.processor.decode(output[0], skip_special_tokens=True),
            EXPECTED_DECODED_TEXT,
        )

    @slow
    @require_bitsandbytes
    def test_small_model_integration_test_llama_single(self):
        # Let' s make sure we test the preprocessing to replace what is used
        model_id = "rhymes-ai/Aria"

        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)
        processor = AutoProcessor.from_pretrained(model_id)

        prompt = "USER: <image>\nWhat are the things I should be cautious about when I visit this place? ASSISTANT:"
        image_file = "https://aria-vl.github.io/static/images/view.jpg"
        raw_image = Image.open(requests.get(image_file, stream=True).raw)
        inputs = processor(images=raw_image, text=prompt, return_tensors="pt").to(torch_device, torch.float16)

        output = model.generate(**inputs, max_new_tokens=900, do_sample=False)
        EXPECTED_DECODED_TEXT = "USER:  \nWhat are the things I should be cautious about when I visit this place? ASSISTANT: When visiting this place, which is a pier or dock extending over a body of water, there are a few things to be cautious about. First, be aware of the weather conditions, as sudden changes in weather can make the pier unsafe to walk on. Second, be mindful of the water depth and any potential hazards, such as submerged rocks or debris, that could cause accidents or injuries. Additionally, be cautious of the tides and currents, as they can change rapidly and pose a risk to swimmers or those who venture too close to the edge of the pier. Finally, be respectful of the environment and other visitors, and follow any posted rules or guidelines for the area."  # fmt: skip

        self.assertEqual(
            processor.decode(output[0], skip_special_tokens=True),
            EXPECTED_DECODED_TEXT,
        )

    @slow
    @require_bitsandbytes
    def test_small_model_integration_test_llama_batched(self):
        # Let' s make sure we test the preprocessing to replace what is used
        model_id = "rhymes-ai/Aria"

        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)
        processor = AutoProcessor.from_pretrained(model_id)

        prompts = [
            "USER: <image>\nWhat are the things I should be cautious about when I visit this place? What should I bring with me? ASSISTANT:",
            "USER: <image>\nWhat is this? ASSISTANT:",
        ]
        image1 = Image.open(requests.get("https://aria-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(requests.get("http://images.cocodataset.org/val2017/000000039769.jpg", stream=True).raw)

        inputs = processor(images=[image1, image2], text=prompts, return_tensors="pt", padding=True)

        output = model.generate(**inputs, max_new_tokens=20)

        EXPECTED_DECODED_TEXT = ['USER:  \nWhat are the things I should be cautious about when I visit this place? What should I bring with me? ASSISTANT: When visiting this place, which is a pier or dock extending over a body of water, you', 'USER:  \nWhat is this? ASSISTANT: The image features two cats lying down on a pink couch. One cat is located on']  # fmt: skip

        self.assertEqual(
            processor.batch_decode(output, skip_special_tokens=True),
            EXPECTED_DECODED_TEXT,
        )

    @slow
    @require_bitsandbytes
    def test_small_model_integration_test_batch(self):
        # Let' s make sure we test the preprocessing to replace what is used
        model = AriaForConditionalGeneration.from_pretrained("rhymes-ai/Aria", load_in_4bit=True)
        # The first batch is longer in terms of text, but only has 1 image. The second batch will be padded in text, but the first will be padded because images take more space!.
        prompts = [
            "USER: <image>\nWhat are the things I should be cautious about when I visit this place? What should I bring with me?\nASSISTANT:",
            "USER: <image>\nWhat is this?\nASSISTANT:",
        ]
        image1 = Image.open(requests.get("https://aria-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(requests.get("http://images.cocodataset.org/val2017/000000039769.jpg", stream=True).raw)

        inputs = self.processor(images=[image1, image2], text=prompts, return_tensors="pt", padding=True)

        output = model.generate(**inputs, max_new_tokens=20)

        EXPECTED_DECODED_TEXT = [
            'USER:  \nWhat are the things I should be cautious about when I visit this place? What should I bring with me?\nASSISTANT: When visiting this place, there are a few things to be cautious about and items to bring.',
            'USER:  \nWhat is this?\nASSISTANT: Cats'
        ]  # fmt: skip
        self.assertEqual(
            self.processor.batch_decode(output, skip_special_tokens=True),
            EXPECTED_DECODED_TEXT,
        )

    @slow
    @require_bitsandbytes
    def test_small_model_integration_test_llama_batched_regression(self):
        # Let' s make sure we test the preprocessing to replace what is used
        model_id = "rhymes-ai/Aria"

        # Multi-image & multi-prompt (e.g. 3 images and 2 prompts now fails with SDPA, this tests if "eager" works as before)
        model = AriaForConditionalGeneration.from_pretrained(
            model_id, load_in_4bit=True, attn_implementation="eager"
        )
        processor = AutoProcessor.from_pretrained(model_id, pad_token="<pad>")

        prompts = [
            "USER: <image>\nWhat are the things I should be cautious about when I visit this place? What should I bring with me?\nASSISTANT:",
            "USER: <image>\nWhat is this?\nASSISTANT: Two cats lying on a bed!\nUSER: <image>\nAnd this?\nASSISTANT:",
        ]
        image1 = Image.open(requests.get("https://aria-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(requests.get("http://images.cocodataset.org/val2017/000000039769.jpg", stream=True).raw)

        inputs = processor(images=[image1, image2, image1], text=prompts, return_tensors="pt", padding=True)

        output = model.generate(**inputs, max_new_tokens=20)

        EXPECTED_DECODED_TEXT = ['USER:  \nWhat are the things I should be cautious about when I visit this place? What should I bring with me?\nASSISTANT: When visiting this place, which appears to be a dock or pier extending over a body of water', 'USER:  \nWhat is this?\nASSISTANT: Two cats lying on a bed!\nUSER:  \nAnd this?\nASSISTANT: A cat sleeping on a bed.']  # fmt: skip

        self.assertEqual(
            processor.batch_decode(output, skip_special_tokens=True),
            EXPECTED_DECODED_TEXT,
        )

    @slow
    @require_torch
    @require_vision
    def test_batched_generation(self):
        model = AriaForConditionalGeneration.from_pretrained("rhymes-ai/Aria", load_in_4bit=True)

        processor = AutoProcessor.from_pretrained("rhymes-ai/Aria")

        prompt1 = "<image>\n<image>\nUSER: What's the the difference of two images?\nASSISTANT:"
        prompt2 = "<image>\nUSER: Describe the image.\nASSISTANT:"
        prompt3 = "<image>\nUSER: Describe the image.\nASSISTANT:"
        url1 = "https://images.unsplash.com/photo-1552053831-71594a27632d?q=80&w=3062&auto=format&fit=crop&ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D"
        url2 = "https://images.unsplash.com/photo-1617258683320-61900b281ced?q=80&w=3087&auto=format&fit=crop&ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D"
        image1 = Image.open(requests.get(url1, stream=True).raw)
        image2 = Image.open(requests.get(url2, stream=True).raw)

        inputs = processor(
            images=[image1, image2, image1, image2],
            text=[prompt1, prompt2, prompt3],
            return_tensors="pt",
            padding=True,
        ).to(torch_device)

        model = model.eval()

        EXPECTED_OUTPUT = [
            "\n \nUSER: What's the the difference of two images?\nASSISTANT: The difference between the two images is that one shows a dog standing on a grassy field, while",
            "\nUSER: Describe the image.\nASSISTANT: The image features a brown and white dog sitting on a sidewalk. The dog is holding a small",
            "\nUSER: Describe the image.\nASSISTANT: The image features a lone llama standing on a grassy hill. The llama is the",
        ]

        generate_ids = model.generate(**inputs, max_new_tokens=20)
        outputs = processor.batch_decode(generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        self.assertEqual(outputs, EXPECTED_OUTPUT)

    @slow
    @require_bitsandbytes
    def test_aria_index_error_bug(self):
        # This is a reproducer of https://github.com/huggingface/transformers/pull/28032 and makes sure it does not happen anymore
        # Please refer to that PR, or specifically https://github.com/huggingface/transformers/pull/28032#issuecomment-1860650043 for
        # more details
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)

        processor = AutoProcessor.from_pretrained(model_id)

        # Simulate a super long prompt
        user_prompt = "Describe the image:?\n" * 200
        prompt = f"USER: <image>\n{user_prompt}ASSISTANT:"
        image_file = "http://images.cocodataset.org/val2017/000000039769.jpg"

        raw_image = Image.open(requests.get(image_file, stream=True).raw)
        inputs = processor(images=raw_image, text=prompt, return_tensors="pt").to(torch_device, torch.float16)

        # Make sure that `generate` works
        _ = model.generate(**inputs, max_new_tokens=20)

    @slow
    @require_torch_gpu
    def test_aria_merge_inputs_error_bug(self):
        # This is a reproducer of https://github.com/huggingface/transformers/pull/28333 and makes sure it does not happen anymore
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)

        # Simulate some user inputs
        pixel_values = torch.randn(
            (1, 3, 336, 336),
            dtype=torch.float,
            device=torch_device,
        )
        input_ids = torch.tensor(
            [
                [32001, 32001, 1, 15043, 7084, 32000, 29871, 13, 7900],
            ],
            dtype=torch.long,
            device=torch_device,
        )
        attention_mask = torch.tensor(
            [[0, 0, 1, 1, 1, 1, 1, 1, 1]],
            dtype=torch.long,
            device=torch_device,
        )

        # Make sure that the loss is properly computed
        loss = model(
            pixel_values=pixel_values,
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=input_ids,
        ).loss
        loss.backward()

    def test_tokenizer_integration(self):
        model_id = "rhymes-ai/Aria"
        slow_tokenizer = AutoTokenizer.from_pretrained(
            model_id, bos_token="<|startoftext|>", eos_token="<|endoftext|>", use_fast=False
        )
        slow_tokenizer.add_tokens("<image>", True)

        fast_tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            bos_token="<|startoftext|>",
            eos_token="<|endoftext|>",
            from_slow=True,
            legacy=False,
        )
        fast_tokenizer.add_tokens("<image>", True)

        prompt = "<|startoftext|><|im_start|>system\nAnswer the questions.<|im_end|><|im_start|>user\n<image>\nWhat is shown in this image?<|im_end|>"
        EXPECTED_OUTPUT = [
            '<|startoftext|>',
            '<',
            '|',
            'im',
            '_',
            'start',
            '|',
            '>',
            'system',
            '\n',
            'Answer',
            '▁the',
            '▁questions',
            '.<',
            '|',
            'im',
            '_',
            'end',
            '|',
            '><',
            '|',
            'im',
            '_',
            'start',
            '|',
            '>',
            'user',
            '\n',
            '<image>',
            '\n',
            'What',
            '▁is',
            '▁shown',
            '▁in',
            '▁this',
            '▁image',
            '?',
            '<',
            '|',
            'im',
            '_',
            'end',
            '|',
            '>'
        ]  # fmt: skip
        self.assertEqual(slow_tokenizer.tokenize(prompt), EXPECTED_OUTPUT)
        self.assertEqual(fast_tokenizer.tokenize(prompt), EXPECTED_OUTPUT)

    @slow
    @require_bitsandbytes
    def test_generation_no_images(self):
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)
        processor = AutoProcessor.from_pretrained(model_id)

        # Prepare inputs with no images
        inputs = processor(text="Hello, I am", return_tensors="pt").to(torch_device)

        # Make sure that `generate` works
        _ = model.generate(**inputs, max_new_tokens=20)

    @slow
    @require_bitsandbytes
    def test_generation_siglip_backbone(self):
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id, torch_dtype="float16", device_map=torch_device)
        processor = AutoProcessor.from_pretrained(model_id)

        # check processing with expansion of inputs (w/o expansion should work with any backbone)
        processor.vision_feature_select_strategy = "default"
        processor.patch_size = 14

        image_file = "http://images.cocodataset.org/val2017/000000039769.jpg"
        raw_image = Image.open(requests.get(image_file, stream=True).raw)
        inputs = processor(
            text="<|im_start|>user\n<image>\nWhat are these?<|im_end|>\n<|im_start|>assistant",
            images=raw_image,
            return_tensors="pt",
        ).to(torch_device, torch.float16)

        # Make sure that `generate` works
        output = model.generate(**inputs, max_new_tokens=30)

        EXPECTED_DECODED_TEXT = "user\n\nWhat are these?\nassistant The image shows two cats, one on the left and one on the right. They appear to be resting or sleeping on a pink blanket. The cat"
        self.assertTrue(processor.batch_decode(output, skip_special_tokens=True)[0] == EXPECTED_DECODED_TEXT)

    @slow
    @require_bitsandbytes
    def test_expansion_in_processing(self):
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id, load_in_4bit=True)
        processor = AutoProcessor.from_pretrained(model_id)

        prompt = "USER: <image>\nDescribe the image:\nASSISTANT:"
        image_file = "http://images.cocodataset.org/val2017/000000039769.jpg"
        raw_image = Image.open(requests.get(image_file, stream=True).raw)

        # check processing with expansion of inputs
        processor.vision_feature_select_strategy = "default"
        processor.patch_size = 14
        inputs_expanded = processor(images=raw_image, text=prompt, return_tensors="pt").to(torch_device, torch.float16)
        self.assertTrue(inputs_expanded.input_ids.shape[-1] == 593)

        # check processing without expansion of inputs (legacy behavior)
        processor.vision_feature_select_strategy = None
        processor.patch_size = None
        inputs = processor(images=raw_image, text=prompt, return_tensors="pt").to(torch_device, torch.float16)
        self.assertTrue(inputs.input_ids.shape[-1] == 18)

        # generate exactly 20 tokens
        output = model.generate(**inputs, min_new_tokens=20, max_new_tokens=20)
        output_expanded = model.generate(**inputs_expanded, min_new_tokens=20, max_new_tokens=20)

        # check that both inputs are handled correctly and generate the same output
        self.assertListEqual(output_expanded[:, -20:].tolist(), output[:, -20:].tolist())

    @slow
    @require_bitsandbytes
    def test_pixtral(self):
        model_id = "rhymes-ai/Aria"
        model = AriaForConditionalGeneration.from_pretrained(model_id)
        processor = AutoProcessor.from_pretrained(model_id)

        IMG_URLS = [
            Image.open(requests.get("https://picsum.photos/id/237/400/300", stream=True).raw),
            Image.open(requests.get("https://picsum.photos/id/231/200/300", stream=True).raw),
            Image.open(requests.get("https://picsum.photos/id/27/500/500", stream=True).raw),
            Image.open(requests.get("https://picsum.photos/id/17/150/600", stream=True).raw),
        ]
        PROMPT = "<s>[INST]Describe the images.\n[IMG][IMG][IMG][IMG][/INST]"

        # image = Image.open(requests.get(url, stream=True).raw)
        inputs = processor(text=PROMPT, images=IMG_URLS, return_tensors="pt").to("cuda")
        generate_ids = model.generate(**inputs, max_new_tokens=500)
        ouptut = processor.batch_decode(generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]

        # fmt: off
        EXPECTED_GENERATION = """
Describe the images.
Sure, let's break down each image description:

1. **Image 1:**
   - **Description:** A black dog with a glossy coat is sitting on a wooden floor. The dog has a focused expression and is looking directly at the camera.
   - **Details:** The wooden floor has a rustic appearance with visible wood grain patterns. The dog's eyes are a striking color, possibly brown or amber, which contrasts with its black fur.

2. **Image 2:**
   - **Description:** A scenic view of a mountainous landscape with a winding road cutting through it. The road is surrounded by lush green vegetation and leads to a distant valley.
   - **Details:** The mountains are rugged with steep slopes, and the sky is clear, indicating good weather. The winding road adds a sense of depth and perspective to the image.

3. **Image 3:**
   - **Description:** A beach scene with waves crashing against the shore. There are several people in the water and on the beach, enjoying the waves and the sunset.
   - **Details:** The waves are powerful, creating a dynamic and lively atmosphere. The sky is painted with hues of orange and pink from the setting sun, adding a warm glow to the scene.

4. **Image 4:**
   - **Description:** A garden path leading to a large tree with a bench underneath it. The path is bordered by well-maintained grass and flowers.
   - **Details:** The path is made of small stones or gravel, and the tree provides a shaded area with the bench invitingly placed beneath it. The surrounding area is lush and green, suggesting a well-kept garden.

Each image captures a different scene, from a close-up of a dog to expansive natural landscapes, showcasing various elements of nature and human interaction with it.
"""
        # fmt: on
        # check that both inputs are handled correctly and generate the same output
        self.assertListEqual(ouptut, EXPECTED_GENERATION)