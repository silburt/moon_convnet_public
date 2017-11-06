######################
#MOON_UNET_S256_RINGS#
############################################
#This model:
#a) uses a custom loss (separately, i.e. *not* differentiable and guiding backpropagation) to assess how well our algorithm is doing, by connecting the predicted circles to the "ground truth" circles
#b) trained using the original LU78287GT.csv values as the ground truth,
#c) uses the Unet model architechture applied on binary rings.

#This model uses keras version 1.2.2.
############################################

import os
import glob
import numpy as np
import pandas as pd
import random
from skimage.feature import match_template

from keras.models import Sequential, Model
from keras.layers.core import Dense, Dropout, Flatten, Reshape
from keras.layers import AveragePooling2D, merge, Input
from keras.layers.convolutional import Convolution2D, MaxPooling2D, UpSampling2D
from keras.regularizers import l2
from keras.models import load_model

from keras.optimizers import SGD, Adam, RMSprop
from keras.callbacks import EarlyStopping, ModelCheckpoint
from keras.utils import np_utils
from keras import __version__ as keras_version
from keras import backend as K
K.set_image_dim_ordering('tf')

from utils.rescale import *
from utils.template_match_target import *

#############################
#Load/Read/Process Functions#
########################################################################
def get_param_i(param,i):
    if len(param) > i:
        return param[i]
    else:
        return param[0]

########################
#Custom Image Generator#
########################################################################
#Following https://github.com/fchollet/keras/issues/2708
def custom_image_generator(data, target, batch_size=32):
    L, W = data[0].shape[0], data[0].shape[1]
    while True:
        for i in range(0, len(data), batch_size):
            d, t = data[i:i+batch_size].copy(), target[i:i+batch_size].copy() #most efficient for memory?
            
            #random color inversion
#            for j in np.where(np.random.randint(0,2,batch_size)==1)[0]:
#                d[j][d[j] > 0.] = 1. - d[j][d[j] > 0.]

            #horizontal/vertical flips
            for j in np.where(np.random.randint(0,2,batch_size)==1)[0]:
                d[j], t[j] = np.fliplr(d[j]), np.fliplr(t[j])               #left/right
            for j in np.where(np.random.randint(0,2,batch_size)==1)[0]:
                d[j], t[j] = np.flipud(d[j]), np.flipud(t[j])               #up/down
            
            #random up/down & left/right pixel shifts, 90 degree rotations
            npix = 15
            h = np.random.randint(-npix,npix+1,batch_size)                  #horizontal shift
            v = np.random.randint(-npix,npix+1,batch_size)                  #vertical shift
            r = np.random.randint(0,4,batch_size)                           #90 degree rotations
            for j in range(batch_size):
                d[j] = np.pad(d[j], ((npix,npix),(npix,npix),(0,0)), mode='constant')[npix+h[j]:L+h[j]+npix,npix+v[j]:W+v[j]+npix,:] #RGB
                t[j] = np.pad(t[j], (npix,), mode='constant')[npix+h[j]:L+h[j]+npix,npix+v[j]:W+v[j]+npix]
                d[j], t[j] = np.rot90(d[j],r[j]), np.rot90(t[j],r[j])
            yield (d, t)

#######################
#Calculate Custom Loss#
########################################################################
def get_metrics(dir, dim, model, data):
    
    X, Y, ids = data[0], data[1], data[2]
    
    # get csvs
    csvs = []
    minrad, maxrad, cutrad = 2, 50, 1
    for i_r in range(len(X)):
        csv_name = '%s/lola_%s.csv'%(dir,str(ids[i_r]).zfill(5))
        csv = pd.read_csv(csv_name)
        # remove small/large/half craters
        csv = csv[(csv['Diameter (pix)'] < 2*maxrad) & (csv['Diameter (pix)'] > 2*minrad)]
        csv = csv[(csv['x']+cutrad*csv['Diameter (pix)']/2 <= dim)]
        csv = csv[(csv['y']+cutrad*csv['Diameter (pix)']/2 <= dim)]
        csv = csv[(csv['x']-cutrad*csv['Diameter (pix)']/2 > 0)]
        csv = csv[(csv['y']-cutrad*csv['Diameter (pix)']/2 > 0)]
        if len(csv) < 3:    #exclude csvs with tiny crater numbers
            csvs.append([-1])
        else:
            csv_coords = np.asarray((csv['x'],csv['y'],csv['Diameter (pix)']/2)).T
            csvs.append(csv_coords)
    
    # calculate custom metrics
    print ""
    print "*********Custom Loss*********"
    recall, precision, f2, frac_new, frac_new2, maxrad = [], [], [], [], [], []
    for i in range(nimgs):
        if len(csvs[i]) < 3:
            continue
        N_match, N_csv, N_templ, maxr, csv_duplicate_flag = template_match_target_to_csv(preds[i], csvs[i], minrad, maxrad, match_thresh2, template_thresh, target_thresh)
        if N_match > 0:
            p = float(N_match)/float(N_match + (N_templ-N_match))   #assuming all unmatched detected circles are false positives
            r = float(N_match)/float(N_csv)                         #N_csv = tp + fn, i.e. total ground truth matches
            f2score = 5*r*p/(4*p+r)                                 #f-score with beta = 2
            fn = float(N_templ - N_match)/float(N_templ)
            fn2 = float(N_templ - N_match)/float(N_csv)
            recall.append(r); precision.append(p); f2.append(f2score)
            frac_new.append(fn); frac_new2.append(fn2); maxrad.apend(maxr)
        else:
            print "skipping iteration %d,N_csv=%d,N_templ=%d,N_match=%d"%(i,N_csv,N_templ,N_match)

    print "binary XE score = %f"%model.evaluate(X.astype('float32'), Y.astype('float32'))
    print "mean and std of N_match/N_csv (recall) = %f, %f"%(np.mean(recall), np.std(recall))
    print "mean and std of N_match/(N_match + (N_templ-N_match)) (precision) = %f, %f"%(np.mean(precision), np.std(precision))
    print "mean and std of 5rp/(2r+p) (F2 score) = %f, %f"%(np.mean(f2), np.std(f2))

    print "mean and std of (N_template - N_match)/N_template (fraction of craters that are new) = %f, %f"%(np.mean(frac_new), np.std(frac_new))
    print "mean and std of (N_template - N_match)/N_csv (fraction of craters that are new, 2) = %f, %f"%(np.mean(frac_new2), np.std(frac_new2))
    print "mean and std of maximum detected pixel radius in an image = %f, %f"%(np.mean(maxrad), np.std(maxrad))
    print "absolute maximum detected pixel radius over all images = %f"%np.max(maxrad)
    print ""

##########################
#Unet Model (keras 1.2.2)#
########################################################################
#Following https://arxiv.org/pdf/1505.04597.pdf
#and this for merging specifics: https://gist.github.com/Neltherion/f070913fd6284c4a0b60abb86a0cd642
def unet_model(dim,learn_rate,lmbda,drop,FL,init,n_filters):
    print('Making UNET model...')
    img_input = Input(batch_shape=(None, dim, dim, 1))
    
    a1 = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(img_input)
    a1 = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a1)
    a1P = MaxPooling2D((2, 2), strides=(2, 2))(a1)
    
    a2 = Convolution2D(n_filters*2, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a1P)
    a2 = Convolution2D(n_filters*2, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a2)
    a2P = MaxPooling2D((2, 2), strides=(2, 2))(a2)
    
    a3 = Convolution2D(n_filters*4, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a2P)
    a3 = Convolution2D(n_filters*4, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a3)
    a3P = MaxPooling2D((2, 2), strides=(2, 2),)(a3)
    
    u = Convolution2D(n_filters*4, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(a3P)
    u = Convolution2D(n_filters*4, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    
    u = UpSampling2D((2,2))(u)
    u = merge((a3, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters*2, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters*2, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    
    u = UpSampling2D((2,2))(u)
    u = merge((a2, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    
    u = UpSampling2D((2,2))(u)
    u = merge((a1, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init, W_regularizer=l2(lmbda), border_mode='same')(u)
    
    #final output
    final_activation = 'sigmoid'
    u = Convolution2D(1, 1, 1, activation=final_activation, init=init, W_regularizer=l2(lmbda), name='output', border_mode='same')(u)
    u = Reshape((dim, dim))(u)
    model = Model(input=img_input, output=u)
    
    #optimizer/compile
    optimizer = Adam(lr=learn_rate, beta_1=0.9, beta_2=0.999, epsilon=1e-08, decay=0.0)
    model.compile(loss='binary_crossentropy', optimizer=optimizer)
    print model.summary()
    
    return model

##################
#Train/Test Model#
########################################################################
#Need to create this function so that memory is released every iteration (when function exits).
#Otherwise the memory used accumulates and eventually the program crashes.
def train_and_test_model(Data,MP,i_MP):
    
    # static params
    dir, dim, learn_rate, nb_epoch, bs = MP['dir'], MP['dim'], MP['lr'], MP['epochs'], MP['bs']
    
    # iterating params
    lmbda = get_param_i(MP['lambda'],i_MP)
    drop = get_param_i(MP['dropout'],i_MP)
    FL = get_param_i(MP['filter_length'],i_MP)
    init = get_param_i(MP['init'],i_MP)
    n_filters = get_param_i(MP['n_filters'],i_MP)
    
    # build model
    model = unet_model(dim,learn_rate,lmbda,drop,FL,init,n_filters)
    
    # main loop
    n_samples = MP['n_train']
    for nb in range(nb_epoch):
        model.fit_generator(custom_image_generator(Data['train'][0],Data['train'][1],batch_size=bs),
                            samples_per_epoch=n_samples,nb_epoch=1,verbose=1,
                            #validation_data=(Data['valid'][0], Data['valid'][1]), #no generator for validation data
                            validation_data=custom_image_generator(Data['valid'][0],Data['valid'][1],batch_size=bs),
                            nb_val_samples=n_samples,
                            callbacks=[EarlyStopping(monitor='val_loss', patience=3, verbose=0)])
    
        valid_dir = '%s/Dev_rings/'%dir
        get_metrics(valid_dir, dim, model, Data['valid'])

    if MP['save_models'] == 1:
        model.save('models/unet_s256_rings_n112_L%.1e_D%.2f.h5'%(lmbda,drop))

    print '###################################'
    print '##########END_OF_RUN_INFO##########'
    print 'learning_rate=%e, batch_size=%d, filter_length=%e, n_epoch=%d, n_train=%d, img_dimensions=%d, rescale=%d, init=%s, n_filters=%d, lambda=%e, dropout=%f'%(learn_rate,bs,FL,nb_epoch,MP['n_train'],MP['dim'],MP['rescale'],init,n_filters,lmbda,drop)
    test_dir = '%s/Test_rings/'%dir
    get_metrics(test_dir, dim, model, Data['test'])
    print '###################################'
    print '###################################'

##################
#Load Data, Train#
########################################################################
def build_model(MP):
    
    dir, dim = MP['dir'], MP['dim']
    n_train, n_valid, n_test = MP['n_train'], MP['n_valid'], MP['n_test']

    #Load data
    train_data=np.load('%s/Train_rings/train_data.npy'%dir)[:n_train]
    train_target=np.load('%s/Train_rings/train_target.npy'%dir)[:n_train]
    train_ids = np.load('%s/Train_rings/train_ids.npy'%dir)[:n_train]
    valid_data=np.load('%s/Dev_rings/dev_data.npy'%dir)[:n_valid]
    valid_target=np.load('%s/Dev_rings/dev_target.npy'%dir)[:n_valid]
    valid_ids = np.load('%s/Dev_rings/dev_ids.npy'%dir)[:n_valid]
    test_data=np.load('%s/Test_rings/test_data.npy'%dir)[:n_test]
    test_target=np.load('%s/Test_rings/test_target.npy'%dir)[:n_test]
    test_ids = np.load('%s/Test_rings/test_ids.npy'%dir)[:n_test]

    #Rescale pixel values to increase contrast
    if MP['rescale']==1:
        train_data = rescale(train_data)
        valid_data = rescale(valid_data)
        test_data = rescale(test_data)

    #Compact data
    Data = {
        'train': [train_data, train_target, train_ids],
        'valid': [valid_data, valid_target, valid_ids],
        'test': [test_data, test_target, test_ids],
    }

    #Iterate
    for i in range(MP['N_runs']):
        train_and_test_model(Data,MP,i)

################
#Arguments, Run#
########################################################################
if __name__ == '__main__':
    print('Keras version: {}'.format(keras_version))
    MP = {}
    
    #Location of Train_rings/, Dev_rings/, Test_rings/ folders.
    #Don't include final '/' in path
    MP['dir'] = 'datasets'
    
    #Model Parameters
    MP['dim'] = 256             #image width/height, assuming square images. Shouldn't change
    MP['lr'] = 0.0001           #learning rate
    MP['bs'] = 8                #batch size: smaller values = less memory but less accurate gradient estimate
    MP['epochs'] = 4            #number of epochs. 1 epoch = forward/back pass through all train data
    MP['n_train'] = 30000       #number of training samples, needs to be a multiple of batch size. Big memory hog.
    MP['n_valid'] = 1000        #number of examples to calculate recall on after each epoch. Expensive operation.
    MP['n_test'] = 5000         #number of examples to calculate recall on after training. Expensive operation.
    MP['rescale'] = 1           #rescale images to increase contrast (still 0-1 normalized)
    MP['save_models'] = 1       #save keras models upon training completion
    
    #Model Parameters (to potentially iterate over, keep in lists)
    MP['N_runs'] = 1
    MP['filter_length'] = [3]
    MP['n_filters'] = [112]
    MP['init'] = ['he_normal']                      #See unet model. Initialization of weights.
    MP['lambda'] = [1e-6]
    MP['dropout'] = [0.15]
    
    #example for iterating over parameters
    #MP['N_runs'] = 4
    #MP['lambda']=[1e-5,1e-5,1e-6,1e-6]              #regularization
    #MP['dropout']=[0.25,0.15,0.25,0.15]             #dropout after merge layers
    
    #run models
    build_model(MP)
