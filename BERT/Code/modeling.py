# 以下对《BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding》简称论文
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy
import json
import math
import six
import torch
import torch.nn as nn
from torch.nn import CrossEntropyLoss

def gelu(x):
    '''
    GELU激活函数，即高斯误差线性单元
    '''
    return x * 0.5 * (1.0 + torch.erf(x / math.sqrt(2.0)))

class BertConfig(object):
    '''
    初始化BERT模型的各种超参数
    得到当前类实例的各种超参数
    '''
    def __init__(self,
                vocab_size, # 论文Figure2中的Token Embeddings层的输入维度
                hidden_size=768,
                num_hidden_layers=12,
                num_attention_heads=12,
                intermediate_size=3072,
                hidden_act="gelu",
                hidden_dropout_prob=0.1,
                attention_probs_dropout_prob=0.1,
                max_position_embeddings=512, # 论文Figure2中的Position Embeddings层的输入维度
                type_vocab_size=16, # 论文Figure2中的Segment Embeddings层的输入维度
                initializer_range=0.02):
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.hidden_act = hidden_act
        self.intermediate_size = intermediate_size
        self.hidden_dropout_prob = hidden_dropout_prob
        self.attention_probs_dropout_prob = attention_probs_dropout_prob
        self.max_position_embeddings = max_position_embeddings
        self.type_vocab_size = type_vocab_size
        self.initializer_range = initializer_range

    @classmethod
    def from_dict(cls, json_object):
        '''
        类方法，从字典构建BertConfig类实例
        '''
        config = BertConfig(vocab_size=None)
        for (key, value) in six.iteritems(json_object):
            config.__dict__[key] = value
        return config

    @classmethod
    def from_json_file(cls, json_file):
        '''
        类方法，从.json构建BertConfig类实例
        '''
        with open(json_file, "r") as reader:
            text = reader.read()
        return cls.from_dict(json.loads(text))

    def to_dict(self):
        '''
        实例方法，将当前的类实例转换为字典
        '''
        output = copy.deepcopy(self.__dict__)
        return output

    def to_json_string(self):
        '''
        实例方法，将当前的类实例转换为.json
        '''
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

class BERTLayerNorm(nn.Module):
    '''
    实现Layer Normalization
    '''
    def __init__(self, config, variance_epsilon=1e-12):
        '''
        其中self.gamma和self.beta是学习参数
        '''
        super(BERTLayerNorm, self).__init__()
        self.gamma = nn.Parameter(torch.ones(config.hidden_size))
        self.beta = nn.Parameter(torch.zeros(config.hidden_size))
        self.variance_epsilon = variance_epsilon

    def forward(self, x):
        u = x.mean(-1, keepdim=True)
        s = (x - u).pow(2).mean(-1, keepdim=True)
        x = (x - u) / torch.sqrt(s + self.variance_epsilon)
        return self.gamma * x + self.beta

class BERTEmbeddings(nn.Module):
    '''
    实现论文中的Figure2
    '''
    def __init__(self, config):
        '''
        nn.Embedding()中含有学习参数
        '''
        super(BERTEmbeddings, self).__init__()
        self.word_embeddings = nn.Embedding(config.vocab_size, config.hidden_size)
        self.position_embeddings = nn.Embedding(config.max_position_embeddings, config.hidden_size)
        self.token_type_embeddings = nn.Embedding(config.type_vocab_size, config.hidden_size)
        self.LayerNorm = BERTLayerNorm(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(self, input_ids, token_type_ids=None):
        '''
        input_ids(tensor):batch_size x seq_length
        position_ids(tensor):batch_size x seq_length
        if token_type_ids == None:
            token_type_ids(tensor): batch_size x seq_length
        embeddings(tensor):batch_size x seq_length x hidden_size
        '''
        seq_length = input_ids.size(1)

        position_ids = torch.arange(seq_length, dtype=torch.long, device=input_ids.device)
        position_ids = position_ids.unsqueeze(0).expand_as(input_ids)

        if token_type_ids is None:
            token_type_ids = torch.zeros_like(input_ids)

        words_embeddings = self.word_embeddings(input_ids)
        position_embeddings = self.position_embeddings(position_ids)
        token_type_embeddings = self.token_type_embeddings(token_type_ids)

        embeddings = words_embeddings + position_embeddings + token_type_embeddings

        embeddings = self.LayerNorm(embeddings)

        embeddings = self.dropout(embeddings)

        return embeddings

# --- --- --- #

class BERTSelfAttention(nn.Module):
    '''
    自注意机制
    '''
    def __init__(self, config):
        '''
        nn.Linear()中含有学习参数
        '''
        super(BERTSelfAttention, self).__init__()
        if config.hidden_size % config.num_attention_heads != 0:
            raise ValueError(
                "The hidden size (%d) is not a multiple of the number of attention "
                "heads (%d)" % (config.hidden_size, config.num_attention_heads))

        self.num_attention_heads = config.num_attention_heads
        self.attention_head_size = int(config.hidden_size / config.num_attention_heads)
        self.all_head_size = self.num_attention_heads * self.attention_head_size

        self.query = nn.Linear(config.hidden_size, self.all_head_size)
        self.key = nn.Linear(config.hidden_size, self.all_head_size)
        self.value = nn.Linear(config.hidden_size, self.all_head_size)

        self.dropout = nn.Dropout(config.attention_probs_dropout_prob)

    def transpose_for_scores(self, x):
        '''
        转置以适应多头注意力
        x():batch_size x seq_length x all_head_size
        '''

        # batch_size x seq_length x num_attention_heads x attention_head_size
        new_x_shape = x.size()[:-1] + (self.num_attention_heads, self.attention_head_size)

        # batch_size x seq_length x num_attention_heads x attention_head_size
        x = x.view(*new_x_shape)

        # batch_size x num_attention_heads x seq_length x attention_head_size
        return x.permute(0, 2, 1, 3)

    def forward(self, hidden_states, attention_mask):
        '''
        hidden_states():batch_size x seq_length x hidden_size
        attention_mask():batch_size x 1 x 1 x seq_length
        '''

        # 都为：batch_size x seq_length x all_head_size
        mixed_query_layer = self.query(hidden_states)
        mixed_key_layer = self.key(hidden_states)
        mixed_value_layer = self.value(hidden_states)

        # 都为：batch_size x num_attention_heads x seq_length x attention_head_size
        query_layer = self.transpose_for_scores(mixed_query_layer)
        key_layer = self.transpose_for_scores(mixed_key_layer)
        value_layer = self.transpose_for_scores(mixed_value_layer)

        attention_scores = torch.matmul(query_layer, key_layer.transpose(-1, -2)) # batch_size x num_attention_heads x seq_length x seq_length
        attention_scores = attention_scores / math.sqrt(self.attention_head_size)
        attention_scores = attention_scores + attention_mask
        attention_probs = nn.Softmax(dim=-1)(attention_scores)
        attention_probs = self.dropout(attention_probs)
        context_layer = torch.matmul(attention_probs, value_layer) # batch_size x num_attention_heads x seq_length x attention_head_size

        # batch_size x seq_length x num_attention_heads x attention_head_size
        context_layer = context_layer.permute(0, 2, 1, 3).contiguous()

        # batch_size x seq_length x all_head_size
        new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
        context_layer = context_layer.view(*new_context_layer_shape)
        return context_layer


class BERTSelfOutput(nn.Module):
    '''
    Transformer的Encoder的Add & Norm层（第一个子层）
    '''
    def __init__(self, config):
        super(BERTSelfOutput, self).__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.LayerNorm = BERTLayerNorm(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(self, hidden_states, input_tensor):
        '''
        hidden_states():batch_size x seq_length x hidden_size 自注意力层的输出
        input_tensor():batch_size x seq_length x hidden_size 自注意力层的输入
        '''
        hidden_states = self.dense(hidden_states)
        hidden_states = self.dropout(hidden_states)
        hidden_states = self.LayerNorm(hidden_states + input_tensor)
        return hidden_states


class BERTAttention(nn.Module):
    '''
    整合BERTSelfAttention和BERTSelfOutput，以实现
    Transformer的Encoder中的Multi-Head Attention和Add & Norm
    '''
    def __init__(self, config):
        super(BERTAttention, self).__init__()
        self.self = BERTSelfAttention(config)
        self.output = BERTSelfOutput(config)

    def forward(self, input_tensor, attention_mask):
        self_output = self.self(input_tensor, attention_mask)
        attention_output = self.output(self_output, input_tensor)
        return attention_output

# --- --- --- #

class BERTIntermediate(nn.Module):
    '''
    Transformer的Encoder的Feed Forward层
    '''
    def __init__(self, config):
        super(BERTIntermediate, self).__init__()
        self.dense = nn.Linear(config.hidden_size, config.intermediate_size)
        self.intermediate_act_fn = gelu

    def forward(self, hidden_states):
        '''
        hidden_states():batch_size x seq_length x hidden_size
        '''

        # batch_size x seq_length x intermediate_size
        hidden_states = self.dense(hidden_states)
        hidden_states = self.intermediate_act_fn(hidden_states)
        return hidden_states


class BERTOutput(nn.Module):
    '''
    Transformer的Encoder的Add & Norm（第二个子层中）
    '''
    def __init__(self, config):
        super(BERTOutput, self).__init__()
        self.dense = nn.Linear(config.intermediate_size, config.hidden_size)
        self.LayerNorm = BERTLayerNorm(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(self, hidden_states, input_tensor):
        '''
        hidden_states():batch_size x seq_length x intermediate_size
        '''

        # batch_size x seq_length x hidden_size
        hidden_states = self.dense(hidden_states)
        hidden_states = self.dropout(hidden_states)
        hidden_states = self.LayerNorm(hidden_states + input_tensor)
        return hidden_states

# --- --- --- #

class BERTLayer(nn.Module):
    '''
    Transformer的Encoder
    '''
    def __init__(self, config):
        super(BERTLayer, self).__init__()
        self.attention = BERTAttention(config)
        self.intermediate = BERTIntermediate(config)
        self.output = BERTOutput(config)

    def forward(self, hidden_states, attention_mask):
        '''
        hidden_states():batch_size x seq_length x hidden_size
        '''
        attention_output = self.attention(hidden_states, attention_mask)
        intermediate_output = self.intermediate(attention_output)
        layer_output = self.output(intermediate_output, attention_output)
        return layer_output

# --- --- --- #

class BERTEncoder(nn.Module):
    '''
    num_hidden_layers个Transformer的Encoder
    '''
    def __init__(self, config):
        super(BERTEncoder, self).__init__()
        layer = BERTLayer(config)
        self.layer = nn.ModuleList([copy.deepcopy(layer) for _ in range(config.num_hidden_layers)])    

    def forward(self, hidden_states, attention_mask):
        '''
        hidden_states():batch_size x seq_length x hidden_size
        attention_mask():batch_size x 1 x 1 x seq_length
        '''
        all_encoder_layers = []
        for layer_module in self.layer:
            hidden_states = layer_module(hidden_states, attention_mask)
            all_encoder_layers.append(hidden_states)
        return all_encoder_layers # 包含所有Encoder层输出的列表

# --- --- --- #

class BERTPooler(nn.Module):
    '''
    池化层，从BERT编码器的输出中提取特征，通常是选择序列中的第一个token，即[CLS token]
    '''
    def __init__(self, config):
        super(BERTPooler, self).__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.activation = nn.Tanh()

    def forward(self, hidden_states):
        '''
        hidden_states():batch_size x seq_length x hidden_size
        '''

        # batch_size x hidden_size
        first_token_tensor = hidden_states[:, 0]

        pooled_output = self.dense(first_token_tensor)
        pooled_output = self.activation(pooled_output)
        return pooled_output

# --- --- --- #

class BertModel(nn.Module):
    def __init__(self, config: BertConfig):
        super(BertModel, self).__init__()
        self.embeddings = BERTEmbeddings(config)
        self.encoder = BERTEncoder(config)
        self.pooler = BERTPooler(config)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        '''
        input_ids(tensor):batch_size x seq_length
        if token_type_ids == None:
            token_type_ids(tensor): batch_size x seq_length
        attention_mask(tensor): batch_size x seq_length
        '''
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        if token_type_ids is None:
            token_type_ids = torch.zeros_like(input_ids)

        # batch_size x 1 x 1 x seq_length
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(dtype=next(self.parameters()).dtype)
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        embedding_output = self.embeddings(input_ids, token_type_ids)
        all_encoder_layers = self.encoder(embedding_output, extended_attention_mask)
        sequence_output = all_encoder_layers[-1]
        pooled_output = self.pooler(sequence_output)

        # all_encoder_layers所有encoder层输出，是一个列表
        # pooled_output池化层的一个序列输出，即[CLS token]
        return all_encoder_layers, pooled_output

# --- --- --- #

class BertForSequenceClassification(nn.Module):
    '''
    文本分类任务：
    论文Figure1中的NSP
    '''
    def __init__(self, config, num_labels):
        super(BertForSequenceClassification, self).__init__()
        self.bert = BertModel(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.classifier = nn.Linear(config.hidden_size, num_labels)

        def init_weights(module):
            if isinstance(module, (nn.Linear, nn.Embedding)):
                module.weight.data.normal_(mean=0.0, std=config.initializer_range)
            elif isinstance(module, BERTLayerNorm):
                module.beta.data.normal_(mean=0.0, std=config.initializer_range)
                module.gamma.data.normal_(mean=0.0, std=config.initializer_range)
            if isinstance(module, nn.Linear):
                module.bias.data.zero_()
        self.apply(init_weights)

    def forward(self, input_ids, token_type_ids, attention_mask, labels=None):
        _, pooled_output = self.bert(input_ids, token_type_ids, attention_mask)
        pooled_output = self.dropout(pooled_output)
        logits = self.classifier(pooled_output)

        if labels is not None:
            loss_fct = CrossEntropyLoss()
            loss = loss_fct(logits, labels)
            return loss, logits
        else:
            return logits

class BertForQuestionAnswering(nn.Module):
    '''
    问答任务：
    论文Figure1中的Start/End Span
    '''
    def __init__(self, config):
        super(BertForQuestionAnswering, self).__init__()
        self.bert = BertModel(config)
        self.qa_outputs = nn.Linear(config.hidden_size, 2)

        def init_weights(module):
            if isinstance(module, (nn.Linear, nn.Embedding)):
                module.weight.data.normal_(mean=0.0, std=config.initializer_range)
            elif isinstance(module, BERTLayerNorm):
                module.beta.data.normal_(mean=0.0, std=config.initializer_range)
                module.gamma.data.normal_(mean=0.0, std=config.initializer_range)
            if isinstance(module, nn.Linear):
                module.bias.data.zero_()
        self.apply(init_weights)

    def forward(self, input_ids, token_type_ids, attention_mask, start_positions=None, end_positions=None):
        '''
        start_position():batch_size
        end_position():batch_size
        如果不是之后也会修改成batch_size
        '''
        all_encoder_layers, _ = self.bert(input_ids, token_type_ids, attention_mask)
        sequence_output = all_encoder_layers[-1]

        logits = self.qa_outputs(sequence_output)
        start_logits, end_logits = logits.split(1, dim=-1)
        start_logits = start_logits.squeeze(-1)
        end_logits = end_logits.squeeze(-1)

        if start_positions is not None and end_positions is not None:
            if len(start_positions.size()) > 1:
                start_positions = start_positions.squeeze(-1)
            if len(end_positions.size()) > 1:
                end_positions = end_positions.squeeze(-1)

            # 限制Start和End在seq_length范伟内
            ignored_index = start_logits.size(1)
            start_positions.clamp_(0, ignored_index)
            end_positions.clamp_(0, ignored_index)

            loss_fct = CrossEntropyLoss(ignore_index=ignored_index)
            start_loss = loss_fct(start_logits, start_positions)
            end_loss = loss_fct(end_logits, end_positions)
            total_loss = (start_loss + end_loss) / 2
            return total_loss
        else:
            # batch_size x seq_length
            return start_logits, end_logits