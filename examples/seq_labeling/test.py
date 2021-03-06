import torch
import torch.nn as nn
from torch.autograd import Variable
import cv2 as cv
import numpy as np
from torchvision import models, transforms

a = torch.load('10')
width = []
height = []

class ResNet():
    def __init__(self):
        self.resnet = models.resnet101(pretrained=True)


def resize_and_pad(bbox_image):
    fixed_height, fixed_width = (39, 377)
    height, width = bbox_image.shape[:2]

    resize_ratio = min(fixed_height / height, fixed_width / width)
    new_size = (int(width * resize_ratio), int(height * resize_ratio))
    resized_image = cv.resize(bbox_image, new_size)

    width_pad, height_pad = (0, 0)
    if new_size[1] == fixed_height:
        width_pad = fixed_width - new_size[0]
    else:
        height_pad = fixed_height - new_size[1]

    width_pad = int(width_pad / 2)
    height_pad = int(height_pad / 2)
    padding_matrix = ((height_pad, height_pad), (width_pad, width_pad), (0, 0))
    padded_image = np.pad(resized_image, padding_matrix, 'constant', constant_values=255)

    resized_image = cv.resize(padded_image, (fixed_width, fixed_height))

    return resized_image


def image_feature(ResNet, image):
    image = transforms.ToTensor()(image)
    x = Variable(torch.unsqueeze(image, dim=0).float(), requires_grad=False)
    y = nn.Linear(1000, 768)(ResNet.resnet(x))
    y = y.data.numpy()
    y = np.squeeze(y)

    return y

RN = ResNet()


for i, page in enumerate(a):
    if i < 11:
        continue
    for j, bbox_image in enumerate(page.bbox_images):
        if not isinstance(bbox_image, str):
            print(i, j)
            shape = bbox_image.shape
            if len(shape) == 3:
                resized_image = resize_and_pad(bbox_image)
                feature = image_feature(RN, resized_image)
                a[i].bbox_images[j] = feature
    torch.save(a, str(i))



import logging
import os
import torch
import cv2 as cv
import torch.nn as nn

from torch.utils.data import Dataset
from torch.autograd import Variable

from torchvision import models, transforms

import numpy as np

logger = logging.getLogger(__name__)


class FunsdDataset(Dataset):
    def __init__(self, args, tokenizer, labels, pad_token_label_id, mode):
        if args.local_rank not in [-1, 0] and mode == "train":
            torch.distributed.barrier()  # Make sure only the first process in distributed training process the
            # dataset, and the others will use the cache

        # Load data features from cache or dataset file
        cached_features_file = os.path.join(
            args.data_dir,
            "cached_{}_{}_{}".format(
                mode,
                list(filter(None, args.model_name_or_path.split("/"))).pop(),
                str(args.max_seq_length),
            ),
        )
        if os.path.exists(cached_features_file) and not args.overwrite_cache:
            logger.info("Loading features from cached file %s", cached_features_file)
            features = torch.load(cached_features_file)
        else:
            logger.info("Creating features from dataset file at %s", args.data_dir)
            ResNet101 = ResNet()
            examples = read_examples_from_file(args.data_dir, mode, ResNet101)
            features = convert_examples_to_features(
                ResNet101,
                examples,
                labels,
                args.max_seq_length,
                tokenizer,
                cls_token_at_end=bool(args.model_type in ["xlnet"]),
                # xlnet has a cls token at the end
                cls_token=tokenizer.cls_token,
                cls_token_segment_id=2 if args.model_type in ["xlnet"] else 0,
                sep_token=tokenizer.sep_token,
                sep_token_extra=bool(args.model_type in ["roberta"]),
                # roberta uses an extra separator b/w pairs of sentences,
                # cf. github.com/pytorch/fairseq/commit/1684e166e3da03f5b600dbb7855cb98ddfcd0805
                pad_on_left=bool(args.model_type in ["xlnet"]),
                # pad on the left for xlnet
                pad_token=tokenizer.convert_tokens_to_ids([tokenizer.pad_token])[0],
                pad_token_segment_id=4 if args.model_type in ["xlnet"] else 0,
                pad_token_label_id=pad_token_label_id,
            )
            if args.local_rank in [-1, 0]:
                logger.info("Saving features into cached file %s", cached_features_file)
                torch.save(features, cached_features_file)

        if args.local_rank == 0 and mode == "train":
            torch.distributed.barrier()  # Make sure only the first process in distributed training process the
            # dataset, and the others will use the cache

        self.features = features
        # Convert to Tensors and build dataset
        self.all_input_ids = torch.tensor(
            [f.input_ids for f in features], dtype=torch.long
        )
        self.all_input_mask = torch.tensor(
            [f.input_mask for f in features], dtype=torch.long
        )
        self.all_segment_ids = torch.tensor(
            [f.segment_ids for f in features], dtype=torch.long
        )
        self.all_label_ids = torch.tensor(
            [f.label_ids for f in features], dtype=torch.long
        )
        self.all_bboxes = torch.tensor(
            [f.boxes for f in features], dtype=torch.long
        )

        # self.all_bbox_images = torch.tensor(
        #     [f.bbox_images for f in features], dtype=torch.float
        # )

    def __len__(self):
        return len(self.features)

    def __getitem__(self, index):
        return (
            self.all_input_ids[index],
            self.all_input_mask[index],
            self.all_segment_ids[index],
            self.all_label_ids[index],
            self.all_bboxes[index]
            #self.all_bbox_images[index]
        )


class InputExample(object):
    """A single training/test example for token classification."""

    def __init__(self, guid, words, labels, boxes, actual_bboxes, image, file_name, page_size):
        """Constructs a InputExample.

        Args:
            guid: Unique id for the example.
            words: list. The words of the sequence.
            labels: (Optional) list. The labels for each word of the sequence. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.words = words
        self.labels = labels
        self.boxes = boxes
        self.actual_bboxes = actual_bboxes
        #self.bbox_images = bbox_images
        self.image = image
        self.file_name = file_name
        self.page_size = page_size


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(
        self,
        input_ids,
        input_mask,
        segment_ids,
        label_ids,
        boxes,
        actual_bboxes,
        #bbox_images,
        file_name,
        page_size,
    ):
        assert (
            0 <= all(boxes) <= 1000
        ), "Error with input bbox ({}): the coordinate value is not between 0 and 1000".format(
            boxes
        )
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_ids = label_ids
        self.boxes = boxes
        self.actual_bboxes = actual_bboxes
        #self.bbox_images = bbox_images
        self.file_name = file_name
        self.page_size = page_size


def resize_and_pad(bbox_image):
    fixed_height, fixed_width = (16, 52)
    height, width = bbox_image.shape[:2]

    resize_ratio = min(fixed_height / height, fixed_width / width)
    new_size = (int(width * resize_ratio), int(height * resize_ratio))
    resized_image = cv.resize(bbox_image, new_size)

    width_pad, height_pad = (0, 0)
    if new_size[1] == fixed_height:
        width_pad = fixed_width - new_size[0]
    else:
        height_pad = fixed_height - new_size[1]

    width_pad = int(width_pad / 2)
    height_pad = int(height_pad / 2)
    padding_matrix = ((height_pad, height_pad), (width_pad, width_pad), (0, 0))
    padded_image = np.pad(resized_image, padding_matrix, 'constant', constant_values=255)

    resized_image = cv.resize(padded_image, (fixed_width, fixed_height))

    return resized_image

class ResNet():
    def __init__(self):
        self.resnet = models.resnet101(pretrained=True)


def image_feature(ResNet, image):
    image = transforms.ToTensor()(image)
    x = Variable(torch.unsqueeze(image, dim=0).float(), requires_grad=False)
    y = nn.Linear(1000, 768)(ResNet.resnet(x))
    y = y.data.numpy()
    y = np.squeeze(y)

    return y


def read_examples_from_file(data_dir, mode, ResNet101):
    file_path = os.path.join(data_dir, "{}.txt".format(mode))
    box_file_path = os.path.join(data_dir, "{}_box.txt".format(mode))
    image_file_path = os.path.join(data_dir, "{}_image.txt".format(mode))

    guid_index = 1
    examples = []

    with open(file_path, encoding="utf-8") as f, \
            open(box_file_path, encoding="utf-8") as fb, \
            open(image_file_path, encoding="utf-8") as fi:
        words = []
        labels = []
        boxes = []
        actual_bboxes = []
        #bbox_images = []
        image = None
        file_name = None
        page_size = None
        for line, bline, iline in zip(f, fb, fi):
            if line.startswith("-DOCSTART-") or line == "" or line == "\n":
                if words:
                    examples.append(
                        InputExample(
                            guid="{}-{}".format(mode, guid_index),
                            words=words,
                            labels=labels,
                            boxes=boxes,
                            actual_bboxes=actual_bboxes,
                            #bbox_images=bbox_images,
                            image=image,
                            file_name=file_name,
                            page_size=page_size,
                        )
                    )
                    print(guid_index)
                    guid_index += 1
                    words = []
                    labels = []
                    boxes = []
                    actual_bboxes = []
                    #bbox_images = []
                    image = None
                    file_name = None
                    page_size = None
            else:
                splits = line.split("\t")
                bsplits = bline.split("\t")
                isplits = iline.split("\t")
                assert len(splits) == 2
                assert len(bsplits) == 2
                assert len(isplits) == 4
                assert splits[0] == bsplits[0]
                words.append(splits[0])
                if len(splits) > 1:
                    labels.append(splits[-1].replace("\n", ""))

                    box = bsplits[-1].replace("\n", "")
                    box = [int(b) for b in box.split()]
                    boxes.append(box)

                    actual_bbox = [int(b) for b in isplits[1].split()]
                    actual_bboxes.append(actual_bbox)

                    page_size = [int(i) for i in isplits[2].split()]
                    file_name = isplits[3].strip()

                    # data_dir = '../../examples/seq_labeling/data/'
                    # file_path = os.path.join(data_dir, mode + 'ing_data/images', file_name)
                    # image = cv.imread(file_path)
                    # bbox_image = image[actual_bbox[1]:actual_bbox[3], actual_bbox[0]:actual_bbox[2]]
                    # bbox_image = np.array(bbox_image)

                    # resized_image = resize_and_pad(bbox_image)

                    # feature = image_feature(ResNet101, resized_image)

                    #bbox_images.append(1)
                else:
                    # Examples could have no label for mode = "test"
                    labels.append("O")
        if words:
            examples.append(
                InputExample(
                    guid="%s-%d".format(mode, guid_index),
                    words=words,
                    labels=labels,
                    boxes=boxes,
                    actual_bboxes=actual_bboxes,
                    #bbox_images=bbox_images,
                    image=image,
                    file_name=file_name,
                    page_size=page_size,
                )
            )
    return examples


def convert_examples_to_features(
    ResNet101,
    examples,
    label_list,
    max_seq_length,
    tokenizer,
    cls_token_at_end=False,
    cls_token="[CLS]",
    cls_token_segment_id=1,
    sep_token="[SEP]",
    sep_token_extra=False,
    pad_on_left=False,
    pad_token=0,
    pad_token_segment_id=0,
    pad_token_label_id=-1,
    cls_token_box=[0, 0, 0, 0],
    sep_token_box=[1000, 1000, 1000, 1000],
    pad_token_box=[0, 0, 0, 0],
    sequence_a_segment_id=0,
    mask_padding_with_zero=True,
):
    """ Loads a data file into a list of `InputBatch`s
        `cls_token_at_end` define the location of the CLS token:
            - False (Default, BERT/XLM pattern): [CLS] + A + [SEP] + B + [SEP]
            - True (XLNet/GPT pattern): A + [SEP] + B + [SEP] + [CLS]
        `cls_token_segment_id` define the segment id associated to the CLS token (0 for BERT, 2 for XLNet)
    """

    label_map = {label: i for i, label in enumerate(label_list)}

    features = []
    for (ex_index, example) in enumerate(examples):
        print(ex_index)
        file_name = example.file_name
        page_size = example.page_size
        image = example.image
        width, height = page_size

        if ex_index % 10000 == 0:
            logger.info("Writing example %d of %d", ex_index, len(examples))

        tokens = []
        label_ids = []
        token_boxes = []
        actual_bboxes = []
        #bbox_images = []

        for word, label, box, actual_bbox in zip(
            example.words, example.labels, example.boxes, example.actual_bboxes
        ):
            word_tokens = tokenizer.tokenize(word)
            tokens.extend(word_tokens)
            token_boxes.extend([box] * len(word_tokens))
            actual_bboxes.extend([actual_bbox] * len(word_tokens))
            #bbox_images.extend([bbox_image] * len(word_tokens))
            # Use the real label id for the first token of the word, and padding ids for the remaining tokens
            label_ids.extend(
                [label_map[label]] + [pad_token_label_id] * (len(word_tokens) - 1)
            )

        # Account for [CLS] and [SEP] with "- 2" and with "- 3" for RoBERTa.
        special_tokens_count = 3 if sep_token_extra else 2
        if len(tokens) > max_seq_length - special_tokens_count:
            tokens = tokens[: (max_seq_length - special_tokens_count)]
            token_boxes = token_boxes[: (max_seq_length - special_tokens_count)]
            actual_bboxes = actual_bboxes[: (max_seq_length - special_tokens_count)]
            #bbox_images = bbox_images[: (max_seq_length - special_tokens_count)]
            label_ids = label_ids[: (max_seq_length - special_tokens_count)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids:   0   0  0    0    0     0       0   0   1  1  1  1   1   1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids:   0   0   0   0  0     0   0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambiguously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens += [sep_token]
        token_boxes += [sep_token_box]
        actual_bboxes += [[0, 0, width, height]]
        # bbox_images += [np.zeros(768)]
        label_ids += [pad_token_label_id]
        if sep_token_extra:
            # roberta uses an extra separator b/w pairs of sentences
            tokens += [sep_token]
            token_boxes += [sep_token_box]
            actual_bboxes += [[0, 0, width, height]]
            #bbox_images += [np.zeros(768)]
            label_ids += [pad_token_label_id]
        segment_ids = [sequence_a_segment_id] * len(tokens)

        if cls_token_at_end:
            tokens += [cls_token]
            token_boxes += [cls_token_box]
            actual_bboxes += [[0, 0, width, height]]
            #bbox_images += ['1']
            label_ids += [pad_token_label_id]
            segment_ids += [cls_token_segment_id]
        else:
            tokens = [cls_token] + tokens
            token_boxes = [cls_token_box] + token_boxes
            actual_bboxes = [[0, 0, width, height]] + actual_bboxes
            #bbox_images = ['1'] + bbox_images
            label_ids = [pad_token_label_id] + label_ids
            segment_ids = [cls_token_segment_id] + segment_ids

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1 if mask_padding_with_zero else 0] * len(input_ids)

        # Zero-pad up to the sequence length.
        padding_length = max_seq_length - len(input_ids)
        if pad_on_left:
            input_ids = ([pad_token] * padding_length) + input_ids
            input_mask = (
                [0 if mask_padding_with_zero else 1] * padding_length
            ) + input_mask
            segment_ids = ([pad_token_segment_id] * padding_length) + segment_ids
            label_ids = ([pad_token_label_id] * padding_length) + label_ids
            token_boxes = ([pad_token_box] * padding_length) + token_boxes
            # bbox_images = ([np.ones(768) * pad_token_label_id] * padding_length) + bbox_images
        else:
            input_ids += [pad_token] * padding_length
            input_mask += [0 if mask_padding_with_zero else 1] * padding_length
            segment_ids += [pad_token_segment_id] * padding_length
            label_ids += [pad_token_label_id] * padding_length
            token_boxes += [pad_token_box] * padding_length
            # bbox_images += [np.ones(768) * pad_token_label_id] * padding_length

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length
        assert len(label_ids) == max_seq_length
        assert len(token_boxes) == max_seq_length
        # assert len(bbox_images) == max_seq_length

        if ex_index < 5:
            logger.info("*** Example ***")
            logger.info("guid: %s", example.guid)
            logger.info("tokens: %s", " ".join([str(x) for x in tokens]))
            logger.info("input_ids: %s", " ".join([str(x) for x in input_ids]))
            logger.info("input_mask: %s", " ".join([str(x) for x in input_mask]))
            logger.info("segment_ids: %s", " ".join([str(x) for x in segment_ids]))
            logger.info("label_ids: %s", " ".join([str(x) for x in label_ids]))
            logger.info("boxes: %s", " ".join([str(x) for x in token_boxes]))
            logger.info("actual_bboxes: %s", " ".join([str(x) for x in actual_bboxes]))
            # logger.info("bbox_images: %s", " ".join([str(x) for x in bbox_images]))

        features.append(
            InputFeatures(
                input_ids=input_ids,
                input_mask=input_mask,
                segment_ids=segment_ids,
                label_ids=label_ids,
                boxes=token_boxes,
                actual_bboxes=actual_bboxes,
                # bbox_images=bbox_images,
                file_name=file_name,
                page_size=page_size,
            )
        )

    return features

