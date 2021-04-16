import time
import datetime
import tensorflow.keras.backend as K
import os
from tqdm import tqdm
import tensorflow as tf
from data_loader.train_loader import TrainLoader
from data_loader.mtat_loader import DataLoader
from model import (
    TagEncoder,
    TagDecoder,
    WaveEncoder,
    WaveProjector,
    SupervisedClassifier,
)

# select GPU
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

# fix random seed
SEED = 42
tf.random.set_seed(SEED)

# define epochs
STAGE1 = 60
STAGE2 = 200


# define input length & batch size
INPUT_LENGTH = 80000
BATCH_SIZE = 16


# define model details
NORMALIZE_EMBEDDING = True
PROJECTION_DIM = 128
ACTIVATION = "leaky_relu"
def contrastive_loss(y, preds, margin=1):
	# explicitly cast the true class label data type to the predicted
	# class label data type (otherwise we run the risk of having two
	# separate data types, causing TensorFlow to error out)
	y = tf.cast(y, preds.dtype)
	# calculate the contrastive loss between the true labels and
	# the predicted labels
	squaredPreds = K.square(preds)
	squaredMargin = K.square(K.maximum(margin - preds, 0))
	loss = K.mean(y * squaredPreds + (1 - y) * squaredMargin)
	# return the computed contrastive loss to the calling function
	return loss

# define models
wave_encoder = WaveEncoder()
wave_projector = WaveProjector(
    PROJECTION_DIM
)
tag_encoder = TagEncoder(activation=ACTIVATION)
tag_decoder = TagDecoder(dimension=50)
classifier = SupervisedClassifier()

# define loss and metrics
bce_loss = tf.keras.losses.BinaryCrossentropy()
mae_loss = tf.keras.losses.MeanSquaredError()
#kld_loss = tf.keras.losses.MeanSquaredError()
#kld_loss = tf.keras.losses.CosineSimilarity()
sparse_loss = tf.keras.losses.KLDivergence()


train_loss = tf.keras.metrics.Mean()
train_loss1 = tf.keras.metrics.Mean()
train_loss2 = tf.keras.metrics.Mean()
train_auc = tf.keras.metrics.AUC()
stage1_loss = tf.keras.metrics.Mean()

valid_loss = tf.keras.metrics.Mean()
valid_auc = tf.keras.metrics.AUC()

test_loss = tf.keras.metrics.Mean()
test_auc = tf.keras.metrics.AUC()

# start time
start_time = time.time()

stage1_test_template = "AELoss : {:.5f}"
stage1_template = "Epoch: {}, TotalLoss : {:.5f},  AELoss : {:.5f}, KLLoss : {:.5f}"
test_template = "Test Loss : {}, Test AUC : {:.2f}%"
valid_template = "\nEpoch: {}, Valid Loss: {:.5f}, Valid AUC: {:.2f}%"
stage2_template = "Epoch : {}, Loss : {:.5f}, AUC : {:.2f}%"


current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
#train_log_dir = "logs/gradient_tape/" + current_time + "/train"
#test_log_dir = "logs/gradient_tape/" + current_time + "/test"
#train_summary_writer = tf.summary.create_file_writer(train_log_dir)
#test_summary_writer = tf.summary.create_file_writer(test_log_dir)

adam = tf.keras.optimizers.Adam(learning_rate=1e-4)
sgd = tf.keras.optimizers.SGD(learning_rate=0.001, momentum=0.9, nesterov=True)
sgd2 = tf.keras.optimizers.SGD(learning_rate=0.001, momentum=0.9, nesterov=True)


def load_data(root="../../tf2-music-tagging-models/dataset"):
    train_data = TrainLoader(root=root, split="train")
    valid_data = DataLoader(root=root, split="valid")
    test_data = DataLoader(root=root, split="test")
    return train_data, valid_data, test_data




@tf.function
def stage1_adam_train_step(wave, labels):
    # apply GradientTape for differentiation
    with tf.GradientTape() as tape:
        # 1. prediction
        # wave
        # z1 shape : (1024,)
        # r1 shape : (128,)
        z1 = wave_encoder(wave, training=True)
        r1 = wave_projector(z1, training=True)

        # tag autoencoder
        # z2 shape : (128,)
        # predictions = (50,)
        z2 = tag_encoder(labels, training=True)
        predictions = tag_decoder(z2, training=True)

        # 2. calculate Loss
        # recon_loss : autoencoder loss
        # repre_loss : loss between rese and autoencoder
        recon_loss = mae_loss(labels, predictions) 
        repre_loss = contrastive_loss(z2, r1)
        total_loss = recon_loss + repre_loss

    train_variable = (
        wave_encoder.trainable_variables
        + wave_projector.trainable_variables
        + tag_encoder.trainable_variables
        + tag_decoder.trainable_variables
    )

    # 3. calculate gradients
    gradients = tape.gradient(total_loss, train_variable)

    # 4. Backpropagation - update weight
    adam.apply_gradients(zip(gradients, train_variable))

    # update loss and accuracy
    train_loss1(recon_loss)
    train_loss2(repre_loss)
    train_loss(total_loss)



@tf.function
def stage1_sgd_train_step(wave, labels):
    with tf.GradientTape() as tape:
        z1 = wave_encoder(wave, training=True)
        r1 = wave_projector(z1, training=True)

        z2 = tag_encoder(labels, training=True)
        predictions = tag_decoder(z2, training=True)

        recon_loss = mae_loss(labels, predictions)
        repre_loss = contrastive_loss(z2, r1)
        total_loss = recon_loss + repre_loss

    train_variable = (
        wave_encoder.trainable_variables
        + wave_projector.trainable_variables
        + tag_encoder.trainable_variables
        + tag_decoder.trainable_variables
    )

    gradients = tape.gradient(total_loss, train_variable)

    sgd.apply_gradients(zip(gradients, train_variable))

    train_loss1(recon_loss)
    train_loss2(repre_loss)
    train_loss(total_loss)


def stage1_train_adam(epochs):
    for epoch in range(epochs):
        for wave, labels in tqdm(train_ds):
            stage1_adam_train_step(wave, labels)

        stage1_log = stage1_template.format(
            epoch + 1, train_loss.result(), train_loss1.result(), train_loss2.result()
        )
        print(stage1_log)


def stage1_train_sgd(epochs):
    for epoch in range(epochs):
        for wave, labels in tqdm(train_ds):
            stage1_sgd_train_step(wave, labels)

        stage1_log = stage1_template.format(
            epoch + 1, train_loss.result(), train_loss1.result(), train_loss2.result()
        )
        print(stage1_log)



@tf.function
def adam_stage2_train_step(wave, labels):
    with tf.GradientTape() as tape:

        z = wave_encoder(wave, training=False)
        predictions = classifier(z, training=True)
        loss = bce_loss(labels, predictions)

    train_variable = classifier.trainable_variables
    gradients = tape.gradient(loss, train_variable)

    adam.apply_gradients(zip(gradients, train_variable))

    train_loss(loss)
    train_auc(labels, predictions)


@tf.function
def sgd_stage2_train_step(wave, labels):
    with tf.GradientTape() as tape:

        z = wave_encoder(wave, training=False)
        predictions = classifier(z, training=True)
        loss = bce_loss(labels, predictions)

    train_variable = classifier.trainable_variables
    gradients = tape.gradient(loss, train_variable)

    sgd2.apply_gradients(zip(gradients, train_variable))

    train_loss(loss)
    train_auc(labels, predictions)

@tf.function
def stage1_test_step(wave, labels):
    z = tag_encoder(labels, training=False)
    predictions = tag_decoder(z, training=False)
    recon_loss = mae_loss(labels, predictions)
    stage1_loss(recon_loss)


@tf.function
def stage2_test_step(wave, labels):
    z = wave_encoder(wave, training=False)
    predictions = classifier(z, training=False)

    loss = bce_loss(labels, predictions)
    valid_loss(loss)
    valid_auc(labels, predictions)


def stage2_train_adam(epochs):
    for epoch in range(epochs):
        for wave, labels in tqdm(train_ds):
            adam_stage2_train_step(wave, labels)

        stage2_log = stage2_template.format(
            epoch + 1, train_loss.result(), train_auc.result()*100
        )
        print(stage2_log)

    if (epoch % 19 == 0 and epoch!= 0):
        for valid_wave, valid_labels in tqdm(valid_ds):
            stage2_test_step(valid_wave, valid_labels)
        valid_log = valid_template.format(epoch+1, valid_loss.result(), valid_auc.result()*100)
        print(valid_log)


def stage2_train_sgd(epochs):
    for epoch in range(epochs):
        for wave, labels in tqdm(train_ds):
            sgd_stage2_train_step(wave, labels)

        stage2_log = stage2_template.format(
            epoch + 1, train_loss.result(), train_auc.result()*100
        )
        print(stage2_log)
    if (epoch % 19 == 0 and epoch!=0):
        for valid_wave, valid_labels in tqdm(valid_ds):
            stage2_test_step(valid_wave, valid_labels)
        valid_log = valid_template.format(epoch+1, valid_loss.result(), valid_auc.result()*100)
        print(valid_log)





# load data
train_ds, valid_ds, test_ds = load_data()

print("@@@@@@@@@@@@@@@@@@@Start training Stage 1@@@@@@@@@@@@@@@@@@\n")
# training

for i in range(3):
    if i == 0:
        epochs = 60
        stage1_train_adam(epochs)
    elif i == 1:
        epochs = 20
        stage1_train_sgd(epochs)
    else:
        epochs = 20
        new_lr = 0.0001
        sgd.lr.assign(new_lr)
        stage1_train_sgd(epochs)

for wave, labels in tqdm(test_ds):
    stage1_test_step(wave, labels)

stage1_test_loss = stage1_test_template.format(stage1_loss.result())
print(stage1_test_loss)

print("\n\n@@@@@@@@@@@@@@@@@@@Start training Stage 2@@@@@@@@@@@@@@@@@@\n")
for i in range(4):
    if i == 0:
        epochs = 60
        stage2_train_adam(epochs)
    elif i == 1:
        epochs = 20
        stage2_train_sgd(epochs)
    elif i ==2:
        epochs = 20
        new_lr = 0.0001
        sgd2.lr.assign(new_lr)
        stage2_train_sgd(epochs)
    else:
        epochs= 100
        new_lr = 0.00001
        sgd2.lr.assign(new_lr)
        stage2_train_sgd(epochs)


"""
# save model
tf.keras.models.save_model(, "./tmp/gpu0_rese/")
tf.keras.models.save_model(classifier, "./tmp/gpu0_classifier/")
"""
# test
for wave, labels in tqdm(test_ds):
    stage2_test_step(wave, labels)

print("Time taken : ", time.time() - start_time)

test_result = test_template.format(valid_loss.result(), valid_auc.result() * 100)
print(test_result)
tf.keras.models.save_model(wave_encoder, "./tmp/wave_encoder")
tf.keras.models.save_model(wave_projector, "./tmp/wave_projector")
tf.keras.models.save_model(tag_encoder, "./tmp/tag_encoder")
tf.keras.models.save_model(tag_decoder, "./tmp/tag_decoder")
tf.keras.models.save_model(classifier, "./tmp/classifier")
